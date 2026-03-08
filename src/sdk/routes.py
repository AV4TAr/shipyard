"""FastAPI routes for the agent-facing SDK API.

These endpoints allow external AI agents to register, discover tasks,
claim work, submit results, and receive structured feedback.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from src.cli.runtime import CLIRuntime
from src.goals.models import GoalStatus, TaskStatus
from src.intent.schema import IntentDeclaration
from src.pipeline.feedback import FeedbackFormatter

from .protocol import (
    AgentRegistration,
    FeedbackMessage,
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
    track this agent's deployment history and trust level.
    """
    rt = _get_runtime()
    # Create / touch the agent profile in the trust tracker
    rt.trust_tracker.get_profile(registration.agent_id)
    _agent_registrations[registration.agent_id] = registration
    return registration


@router.get("/tasks")
def list_available_tasks() -> list[TaskAssignment]:
    """List tasks available for agents to claim.

    Returns pending tasks from all active goals.
    """
    rt = _get_runtime()
    assignments: list[TaskAssignment] = []

    # Gather tasks from active / in-progress goals
    active_goals = rt.goal_manager.list_goals(status=GoalStatus.ACTIVE)
    in_progress_goals = rt.goal_manager.list_goals(status=GoalStatus.IN_PROGRESS)

    for goal in active_goals + in_progress_goals:
        tasks = rt.goal_manager.get_tasks(goal.goal_id)
        for task in tasks:
            if task.status != TaskStatus.PENDING:
                continue
            assignments.append(
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
            )

    return assignments


@router.post("/tasks/{task_id}/claim")
def claim_task(task_id: uuid.UUID) -> TaskAssignment:
    """Claim a task for the requesting agent.

    Marks the task as ASSIGNED and creates an intent declaration so the
    pipeline knows this agent plans to work on these files.
    """
    rt = _get_runtime()

    # Find the task
    try:
        task = rt.goal_manager.update_task_status(task_id, TaskStatus.ASSIGNED)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Look up the parent goal for acceptance criteria
    try:
        goal = rt.goal_manager.get(task.goal_id)
    except KeyError:
        goal = None

    return TaskAssignment(
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


@router.post("/tasks/{task_id}/submit")
def submit_work(task_id: uuid.UUID, submission: WorkSubmission) -> FeedbackMessage:
    """Submit completed work and trigger the pipeline.

    Creates an intent declaration from the submission and runs the full
    5-stage pipeline.  Returns structured feedback.
    """
    rt = _get_runtime()

    # Verify task exists
    try:
        rt.goal_manager.update_task_status(task_id, TaskStatus.IN_PROGRESS)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Build an IntentDeclaration from the submission
    intent = IntentDeclaration(
        agent_id=submission.agent_id,
        intent_id=submission.intent_id,
        description=submission.description,
        rationale=f"Submitted work for task {task_id}: {submission.description}",
        target_files=submission.files_changed,
    )

    # Run the pipeline
    pipeline_run = rt.orchestrator.run(intent, submission.agent_id)

    # Convert pipeline result to feedback
    agent_feedback = _feedback_formatter.format_for_agent(pipeline_run)
    succeeded = agent_feedback.get("succeeded", False)

    if succeeded:
        status = "accepted"
        message = "Work accepted — pipeline passed."
        rt.goal_manager.update_task_status(task_id, TaskStatus.COMPLETED)
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
