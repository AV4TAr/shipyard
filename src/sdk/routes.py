"""FastAPI routes for the agent-facing SDK API.

These endpoints allow external AI agents to register, discover tasks,
claim work, submit results, and receive structured feedback.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException

from src.cli.runtime import CLIRuntime
from src.goals.models import GoalStatus, TaskStatus
from src.intent.schema import IntentDeclaration
from src.leases.manager import AgentPhase
from src.pipeline.feedback import FeedbackFormatter

from .protocol import (
    AgentRegistration,
    FeedbackMessage,
    HeartbeatRequest,
    HeartbeatResponse,
    TaskAssignment,
    WorkSubmission,
)

router = APIRouter(prefix="/api/agents/sdk", tags=["agent-sdk"])

# ---------------------------------------------------------------------------
# Module-level state: set by mount_sdk_routes() so the router can access
# the shared CLIRuntime without FastAPI Depends (since there is no existing
# dependency-injection setup in this codebase yet).
# ---------------------------------------------------------------------------

_runtime: CLIRuntime | None = None
_feedback_store: dict[uuid.UUID, FeedbackMessage] = {}
_agent_registrations: dict[str, AgentRegistration] = {}
_feedback_formatter = FeedbackFormatter()


def mount_sdk_routes(runtime: CLIRuntime) -> APIRouter:
    """Configure the SDK router with the given *runtime* and return it.

    Call this once when building the FastAPI app::

        app.include_router(mount_sdk_routes(runtime))
    """
    global _runtime  # noqa: PLW0603
    _runtime = runtime
    return router


def _get_runtime() -> CLIRuntime:
    """Return the configured runtime or raise."""
    if _runtime is None:
        raise RuntimeError("SDK routes not initialised — call mount_sdk_routes() first")
    return _runtime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/register")
def register_agent(registration: AgentRegistration) -> AgentRegistration:
    """Register an agent with the system.

    Creates an agent profile in the trust tracker so the system can
    track this agent's deployment history and trust level.  Also
    registers the agent with the routing system when available.
    """
    rt = _get_runtime()
    # Create / touch the agent profile in the trust tracker
    rt.trust_tracker.get_profile(registration.agent_id)
    _agent_registrations[registration.agent_id] = registration

    # Bridge into the routing AgentRegistry when available.
    if rt.agent_registry is not None:
        rt.register_agent(
            agent_id=registration.agent_id,
            name=registration.name,
            capabilities=registration.capabilities,
            languages=registration.languages,
            frameworks=registration.frameworks,
            max_concurrent_tasks=registration.max_concurrent_tasks,
        )

    return registration


def _get_paused_goal_ids(rt: "CLIRuntime") -> set:
    """Return the set of goal IDs belonging to paused or cancelled projects."""
    paused_goal_ids: set = set()
    if rt.project_manager is not None:
        try:
            from src.projects.models import ProjectStatus as PS
            for project in rt.project_manager.list_projects():
                if project.status in (PS.PAUSED, PS.CANCELLED):
                    for ms in project.milestones:
                        for gid in ms.goal_ids:
                            paused_goal_ids.add(gid)
        except Exception:
            pass
    return paused_goal_ids


@router.get("/tasks")
def list_available_tasks(agent_id: str = None) -> list[TaskAssignment]:
    """List tasks available for agents to claim.

    Returns pending tasks from all active goals. When *agent_id* is provided,
    tasks are sorted by capability match so each agent sees its best-fit
    tasks first (e.g. QA agent sees test tasks at the top).

    Tasks from paused or cancelled projects are excluded.
    """
    rt = _get_runtime()
    # Collect (task, goal) pairs for available work
    available: list[tuple] = []

    # Exclude goals from paused/cancelled projects
    paused_goal_ids = _get_paused_goal_ids(rt)

    active_goals = rt.goal_manager.list_goals(status=GoalStatus.ACTIVE)
    in_progress_goals = rt.goal_manager.list_goals(status=GoalStatus.IN_PROGRESS)

    for goal in active_goals + in_progress_goals:
        if goal.goal_id in paused_goal_ids:
            continue
        tasks = rt.goal_manager.get_tasks(goal.goal_id)
        task_map = {t.task_id: t for t in tasks}
        for task in tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if task.depends_on:
                deps_met = all(
                    task_map.get(dep_id) and task_map[dep_id].status == TaskStatus.COMPLETED
                    for dep_id in task.depends_on
                )
                if not deps_met:
                    continue
            available.append((task, goal))

    # Sort by capability match when an agent_id is provided
    if agent_id and rt.task_router:
        available.sort(
            key=lambda tg: rt.task_router.score_task_for_agent(tg[0], agent_id),
            reverse=True,
        )

    return [
        TaskAssignment(
            task_id=task.task_id,
            goal_id=task.goal_id,
            title=task.title,
            description=task.description,
            constraints=task.constraints,
            acceptance_criteria=goal.acceptance_criteria,
            target_files=task.target_files,
            estimated_risk=task.estimated_risk.value
            if hasattr(task.estimated_risk, "value")
            else str(task.estimated_risk),
        )
        for task, goal in available
    ]


@router.post("/tasks/{task_id}/claim")
def claim_task(task_id: uuid.UUID, agent_id: Optional[str] = None) -> TaskAssignment:
    """Claim a task for the requesting agent.

    Marks the task as ASSIGNED and, when a LeaseManager is available,
    creates a lease with heartbeat-based renewal.
    """
    rt = _get_runtime()

    # Pipeline freeze check
    if rt.lease_manager is not None and rt.lease_manager.frozen:
        raise HTTPException(
            status_code=503,
            detail="Pipeline is frozen — no new claims allowed",
        )

    # Agent ban check
    if rt.lease_manager is not None and agent_id and rt.lease_manager.is_agent_banned(agent_id):
        raise HTTPException(
            status_code=403,
            detail="Agent {} is banned".format(agent_id),
        )

    # Paused project check — find the task's goal and check project status
    paused_goal_ids = _get_paused_goal_ids(rt)
    task_obj = _find_task(rt, task_id)
    if task_obj is not None and task_obj.goal_id in paused_goal_ids:
        raise HTTPException(
            status_code=409,
            detail="Task belongs to a paused or cancelled project",
        )

    # Find the task
    try:
        task = rt.goal_manager.update_task_status(task_id, TaskStatus.ASSIGNED)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Lease management
    lease_info = None
    if rt.lease_manager is not None and agent_id:
        try:
            lease_info = rt.lease_manager.claim(task_id, agent_id)
        except ValueError as exc:
            # Double-claim — roll task back to PENDING
            try:
                rt.goal_manager.update_task_status(task_id, TaskStatus.PENDING)
            except Exception:
                pass
            raise HTTPException(status_code=409, detail=str(exc))

        # Store lease fields on the task model
        task.claimed_by = agent_id
        task.claimed_at = lease_info.lease_expires_at
        task.lease_expires_at = lease_info.lease_expires_at

    # Worktree creation (Phase 3)
    worktree_path = None
    branch_name = None
    if rt.worktree_manager is not None and agent_id:
        try:
            project = _find_project_for_task(rt, task)
            if project and project.repo_url:
                wt_info = rt.worktree_manager.create_worktree(project, task)
                worktree_path = wt_info["worktree_path"]
                branch_name = wt_info["branch_name"]
                task.worktree_path = worktree_path
                task.branch_name = branch_name
        except Exception:
            pass  # Worktree creation is best-effort

    # Look up the parent goal for acceptance criteria
    try:
        goal = rt.goal_manager.get(task.goal_id)
    except KeyError:
        goal = None

    assignment = TaskAssignment(
        task_id=task.task_id,
        goal_id=task.goal_id,
        title=task.title,
        description=task.description,
        constraints=task.constraints,
        acceptance_criteria=goal.acceptance_criteria if goal else [],
        target_files=task.target_files,
        estimated_risk=task.estimated_risk.value
        if hasattr(task.estimated_risk, "value")
        else str(task.estimated_risk),
    )

    # Add lease fields
    if lease_info is not None:
        assignment.lease_expires_at = lease_info.lease_expires_at
        assignment.lease_duration_seconds = lease_info.lease_duration_seconds
        assignment.heartbeat_interval_seconds = lease_info.heartbeat_interval_seconds

    # Add worktree fields
    if worktree_path:
        assignment.worktree_path = worktree_path
        assignment.branch_name = branch_name

    return assignment


@router.post("/tasks/{task_id}/heartbeat")
def heartbeat(task_id: uuid.UUID, req: HeartbeatRequest) -> HeartbeatResponse:
    """Renew the lease on a claimed task.

    Agents should call this at the interval returned in the claim response
    to keep their lease active. Expired leases cause the task to reset to
    PENDING.
    """
    rt = _get_runtime()

    if rt.lease_manager is None:
        raise HTTPException(
            status_code=501, detail="Lease management not enabled"
        )

    phase = None
    if req.phase:
        try:
            phase = AgentPhase(req.phase)
        except ValueError:
            pass

    try:
        lease = rt.lease_manager.renew(task_id, req.agent_id, phase=phase)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"No active lease for task {task_id}"
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    # Determine if the agent should cancel its work
    should_cancel = False

    # Check pipeline freeze
    if rt.lease_manager.frozen:
        should_cancel = True

    # Check agent ban
    if rt.lease_manager.is_agent_banned(req.agent_id):
        should_cancel = True

    # Check if the task's project is paused or cancelled
    if not should_cancel:
        paused_goal_ids = _get_paused_goal_ids(rt)
        task_obj = _find_task(rt, task_id)
        if task_obj is not None and task_obj.goal_id in paused_goal_ids:
            should_cancel = True

    return HeartbeatResponse(
        task_id=lease.task_id,
        lease_expires_at=lease.lease_expires_at,
        lease_duration_seconds=lease.lease_duration_seconds,
        cancel=should_cancel,
    )


@router.post("/tasks/{task_id}/submit")
def submit_work(task_id: uuid.UUID, submission: WorkSubmission) -> FeedbackMessage:
    """Submit completed work and trigger the pipeline.

    Creates an intent declaration from the submission and runs the full
    5-stage pipeline.  Returns structured feedback.
    """
    rt = _get_runtime()

    # Pipeline freeze check
    if rt.lease_manager is not None and rt.lease_manager.frozen:
        raise HTTPException(
            status_code=503,
            detail="Pipeline is frozen — no new submissions allowed",
        )

    # Verify task exists
    try:
        rt.goal_manager.update_task_status(task_id, TaskStatus.IN_PROGRESS)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Guard: reject if there's already an active pipeline run for this task
    existing_runs = rt.orchestrator.list_runs()
    for run in existing_runs:
        run_meta = getattr(run, "metadata", None) or {}
        run_task = run_meta.get("routed_from_task") or run_meta.get("task_id")
        if run_task == str(task_id) and run.status.value in ("running", "blocked"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Task {task_id} already has an active pipeline run "
                    f"({run.run_id}). Approve or reject it before resubmitting."
                ),
            )

    # If using worktrees, generate diff from git
    diff = submission.diff or ""
    files_changed = list(submission.files_changed)
    if rt.worktree_manager is not None and not diff:
        task_obj = _find_task(rt, task_id)
        if task_obj and task_obj.worktree_path:
            try:
                wt_diff = rt.worktree_manager.get_diff(task_obj.worktree_path)
                if wt_diff:
                    diff = wt_diff
                wt_files = rt.worktree_manager.get_changed_files(
                    task_obj.worktree_path
                )
                if wt_files:
                    files_changed = wt_files
            except Exception:
                pass

    # Build an IntentDeclaration from the submission
    intent = IntentDeclaration(
        agent_id=submission.agent_id,
        intent_id=submission.intent_id,
        description=submission.description,
        rationale=f"Submitted work for task {task_id}: {submission.description}",
        target_files=files_changed,
        metadata={"task_id": str(task_id), "diff": diff},
    )

    # Run the pipeline (may use real tests in worktree)
    pipeline_run = rt.orchestrator.run(intent, submission.agent_id)

    # Store diff and description on the run for UI review
    pipeline_run.metadata["diff"] = diff
    pipeline_run.metadata["description"] = submission.description
    pipeline_run.metadata["files_changed"] = files_changed
    rt.orchestrator._save_run(pipeline_run)

    # Convert pipeline result to feedback
    agent_feedback = _feedback_formatter.format_for_agent(pipeline_run)
    succeeded = agent_feedback.get("succeeded", False)

    if succeeded:
        status = "accepted"
        message = "Work accepted — pipeline passed."
        rt.goal_manager.update_task_status(task_id, TaskStatus.COMPLETED)
        # Release lease on completion
        if rt.lease_manager is not None and submission.agent_id:
            try:
                rt.lease_manager.release(task_id, submission.agent_id)
            except Exception:
                pass
    elif pipeline_run.status.value == "blocked":
        status = "needs_revision"
        message = "Work pending human approval."
    else:
        status = "rejected"
        message = "Work rejected — pipeline failed."
        rt.goal_manager.update_task_status(task_id, TaskStatus.FAILED)

    suggestions = agent_feedback.get("next_actions", [])

    feedback = FeedbackMessage(
        task_id=task_id,
        status=status,
        message=message,
        details={"run_id": str(pipeline_run.run_id)},
        suggestions=suggestions,
        validation_results=agent_feedback,
    )

    # Store for later retrieval
    _feedback_store[task_id] = feedback
    return feedback


@router.get("/tasks/{task_id}/feedback")
def get_feedback(task_id: uuid.UUID) -> FeedbackMessage:
    """Retrieve feedback for a previously submitted task."""
    if task_id not in _feedback_store:
        raise HTTPException(
            status_code=404,
            detail=f"No feedback found for task {task_id}",
        )
    return _feedback_store[task_id]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_task(rt: CLIRuntime, task_id: uuid.UUID):
    """Find an AgentTask by ID across all goals."""
    for goal in rt.goal_manager.list_goals():
        for task in rt.goal_manager.get_tasks(goal.goal_id):
            if task.task_id == task_id:
                return task
    return None


def _find_project_for_task(rt: CLIRuntime, task):
    """Find the project that owns a task (via goal → milestone → project)."""
    if rt.project_manager is None:
        return None
    try:
        projects = rt.project_manager.list_projects()
        for project in projects:
            for ms in project.milestones:
                if task.goal_id in ms.goal_ids:
                    return project
    except Exception:
        pass
    return None
