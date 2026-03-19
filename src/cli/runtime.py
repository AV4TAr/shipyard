"""CLIRuntime — glue layer that wires all system components together.

The CLI commands operate against a single ``CLIRuntime`` instance, which
owns the GoalManager, PipelineOrchestrator, TrustTracker, and every
other subsystem.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from src.coordination.claims import ClaimManager
from src.coordination.queue import DeployQueue
from src.goals.decomposer import GoalDecomposer
from src.goals.manager import GoalManager
from src.goals.models import (
    AgentTask,
    Goal,
    GoalInput,
    GoalPriority,
    GoalStatus,
    TaskBreakdown,
)
from src.intent.registry import IntentRegistry
from src.pipeline.models import PipelineRun, PipelineStatus
from src.pipeline.orchestrator import PipelineOrchestrator
from src.projects.manager import ProjectManager
from src.projects.models import Project, ProjectInput
from src.projects.models import ProjectStatus as ProjStatus
from src.projects.planner import ProjectPlanner
from src.routing.integration import RoutingBridge
from src.routing.models import AgentCapability, RouteDecision
from src.routing.models import AgentRegistration as RoutingAgentRegistration
from src.routing.registry import AgentRegistry
from src.routing.router import TaskRouter
from src.sandbox.manager import SandboxManager
from src.trust.models import AgentProfile
from src.trust.scorer import RiskScorer
from src.trust.tracker import TrustTracker
from src.validation.gate import ValidationGate


def _capability_from_string(value: str) -> AgentCapability:
    """Convert a string capability to :class:`AgentCapability`, falling
    back to ``GENERIC`` for unknown values."""
    try:
        return AgentCapability(value.lower())
    except ValueError:
        return AgentCapability.GENERIC


class CLIRuntime:
    """Central runtime that wires every subsystem together.

    CLI commands call methods on this object rather than reaching into
    subsystems directly.
    """

    def __init__(
        self,
        *,
        goal_manager: GoalManager,
        orchestrator: PipelineOrchestrator,
        trust_tracker: TrustTracker,
        claim_manager: ClaimManager,
        deploy_queue: DeployQueue,
        intent_registry: IntentRegistry,
        agent_registry: AgentRegistry | None = None,
        task_router: TaskRouter | None = None,
        routing_bridge: RoutingBridge | None = None,
        event_dispatcher: Any | None = None,
        project_manager: ProjectManager | None = None,
        lease_manager: Any | None = None,
        worktree_manager: Any | None = None,
    ) -> None:
        self.goal_manager = goal_manager
        self.orchestrator = orchestrator
        self.trust_tracker = trust_tracker
        self.claim_manager = claim_manager
        self.deploy_queue = deploy_queue
        self.intent_registry = intent_registry
        self.agent_registry = agent_registry
        self.task_router = task_router
        self.routing_bridge = routing_bridge
        self.event_dispatcher = event_dispatcher
        self.project_manager = project_manager
        self.lease_manager = lease_manager
        self.worktree_manager = worktree_manager

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_defaults(
        cls,
        *,
        storage_backend: str = "memory",
        db_path: str | None = None,
    ) -> CLIRuntime:
        """Create a runtime with default components and optional persistence.

        When ``storage_backend="sqlite"``, data is persisted to disk via
        SQLite.  The default ``"memory"`` backend keeps everything in-memory.

        The environment variable ``SHIPYARD_DB_PATH`` can also be used to
        enable SQLite persistence without passing arguments explicitly.
        """
        # Allow env-var to override the storage backend
        env_db_path = os.environ.get("SHIPYARD_DB_PATH")
        if env_db_path:
            storage_backend = "sqlite"
            db_path = db_path or env_db_path

        from src.notifications.dispatcher import EventDispatcher
        from src.storage.factory import create_storage

        storage = create_storage(backend=storage_backend, db_path=db_path)
        event_dispatcher = EventDispatcher()

        intent_registry = IntentRegistry(intent_repo=storage.intents)

        sandbox_backend = None
        if os.environ.get("OPENSANDBOX_SERVER_URL"):
            from src.sandbox.backends import OpenSandboxBackend

            sandbox_backend = OpenSandboxBackend()
        sandbox_manager = SandboxManager(backend=sandbox_backend)

        # Worktree manager for git-based code workflows
        from src.worktrees.manager import WorktreeManager

        worktree_manager = WorktreeManager()

        # Wire all real validation runners
        from src.validation.real_runners import (
            RealResourceBoundsRunner,
            RealSecurityScanRunner,
            RealStaticAnalysisRunner,
        )
        from src.validation.signals import BehavioralDiffRunner

        validation_gate = ValidationGate(runners=[
            RealStaticAnalysisRunner(),
            BehavioralDiffRunner(worktree_manager=worktree_manager),
            RealSecurityScanRunner(),
            RealResourceBoundsRunner(),
        ])
        risk_scorer = RiskScorer()
        trust_tracker = TrustTracker(profile_repo=storage.agent_profiles)
        claim_manager = ClaimManager()
        deploy_queue = DeployQueue()

        orchestrator = PipelineOrchestrator(
            intent_registry=intent_registry,
            sandbox_manager=sandbox_manager,
            validation_gate=validation_gate,
            risk_scorer=risk_scorer,
            trust_tracker=trust_tracker,
            claim_manager=claim_manager,
            deploy_queue=deploy_queue,
            run_repo=storage.pipeline_runs,
            event_dispatcher=event_dispatcher,
            worktree_manager=worktree_manager,
        )

        decomposer: GoalDecomposer | Any = GoalDecomposer()
        if os.environ.get("OPENROUTER_API_KEY"):
            try:
                from src.llm.decomposer import LLMGoalDecomposer

                decomposer = LLMGoalDecomposer()
            except Exception:
                pass  # Fall back to rule-based

        goal_manager = GoalManager(
            decomposer=decomposer,
            goal_repo=storage.goals,
            task_repo=storage.tasks,
            event_dispatcher=event_dispatcher,
        )

        agent_registry = AgentRegistry(
            registration_repo=storage.agent_registrations,
        )
        task_router = TaskRouter(agent_registry, trust_tracker=trust_tracker)
        routing_bridge = RoutingBridge(
            task_router, event_dispatcher=event_dispatcher
        )

        project_manager = ProjectManager(
            goal_manager=goal_manager,
            project_repo=storage.projects,
        )

        # Auto-cascade: when a goal completes, check if its milestone is done
        goal_manager._on_goal_completed = project_manager.on_goal_completed

        # Lease manager for heartbeat-based task claims
        from src.leases.manager import LeaseManager

        lease_manager = LeaseManager(
            goal_manager=goal_manager,
            event_dispatcher=event_dispatcher,
        )

        return cls(
            goal_manager=goal_manager,
            orchestrator=orchestrator,
            trust_tracker=trust_tracker,
            claim_manager=claim_manager,
            deploy_queue=deploy_queue,
            intent_registry=intent_registry,
            agent_registry=agent_registry,
            task_router=task_router,
            routing_bridge=routing_bridge,
            event_dispatcher=event_dispatcher,
            project_manager=project_manager,
            lease_manager=lease_manager,
            worktree_manager=worktree_manager,
        )

    @classmethod
    def from_config(cls, config_path: str) -> CLIRuntime:
        """Create a runtime from a JSON configuration file.

        The configuration file may override default settings for the
        pipeline, risk scorer, and other subsystems.  For now this is a
        thin wrapper that reads the config and falls back to defaults
        for any missing keys.
        """
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(path) as fh:
            _config: dict[str, Any] = json.load(fh)

        # TODO: Apply config values to component constructors.
        return cls.from_defaults()

    # ------------------------------------------------------------------
    # Goal commands
    # ------------------------------------------------------------------

    def create_goal(
        self,
        *,
        title: str,
        description: str,
        constraints: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
        priority: str = "medium",
        target_services: list[str] | None = None,
    ) -> Goal:
        """Create a new goal from CLI input."""
        goal_input = GoalInput(
            title=title,
            description=description,
            constraints=constraints or [],
            acceptance_criteria=acceptance_criteria or [],
            priority=GoalPriority(priority),
            target_services=target_services or [],
        )
        return self.goal_manager.create(goal_input, created_by="cli-user")

    def list_goals(
        self,
        status: str | None = None,
        priority: str | None = None,
    ) -> list[Goal]:
        """List goals with optional filters."""
        status_enum = GoalStatus(status) if status else None
        priority_enum = GoalPriority(priority) if priority else None
        return self.goal_manager.list_goals(status=status_enum, priority=priority_enum)

    def show_goal(self, goal_id: str) -> tuple[Goal, list[AgentTask]]:
        """Return a goal and its tasks."""
        uid = uuid.UUID(goal_id)
        goal = self.goal_manager.get(uid)
        tasks = self.goal_manager.get_tasks(uid)
        return goal, tasks

    def activate_goal(self, goal_id: str) -> TaskBreakdown:
        """Activate a goal: decompose into tasks."""
        uid = uuid.UUID(goal_id)
        return self.goal_manager.activate(uid)

    def cancel_goal(self, goal_id: str) -> Goal:
        """Cancel a goal and its outstanding tasks."""
        uid = uuid.UUID(goal_id)
        return self.goal_manager.cancel(uid)

    # ------------------------------------------------------------------
    # Project commands
    # ------------------------------------------------------------------

    def _ensure_project_manager(self) -> ProjectManager:
        """Return the project manager, raising if not configured."""
        if self.project_manager is None:
            raise RuntimeError("ProjectManager is not configured")
        return self.project_manager

    def create_project(
        self,
        *,
        title: str,
        description: str,
        constraints: list[str] | None = None,
        priority: str = "medium",
        target_services: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> Project:
        """Create a new project from CLI/API input."""
        pm = self._ensure_project_manager()
        project_input = ProjectInput(
            title=title,
            description=description,
            constraints=constraints or [],
            priority=GoalPriority(priority),
            target_services=target_services or [],
            tags=tags or [],
        )
        return pm.create(project_input, created_by="cli-user")

    def list_projects(
        self, status: str | None = None
    ) -> list[Project]:
        """List projects with optional status filter."""
        pm = self._ensure_project_manager()
        status_enum = ProjStatus(status) if status else None
        return pm.list_projects(status=status_enum)

    def show_project(self, project_id: str) -> Project:
        """Return a project by ID."""
        pm = self._ensure_project_manager()
        uid = uuid.UUID(project_id)
        return pm.get(uid)

    def activate_project(self, project_id: str) -> Project:
        """Plan milestones and activate a project."""
        pm = self._ensure_project_manager()
        uid = uuid.UUID(project_id)
        project = pm.get(uid)
        if project.status == ProjStatus.DRAFT:
            pm.plan(uid)
            # Only auto-plan milestones if none were manually added
            if not project.milestones:
                planner = ProjectPlanner()
                milestones = planner.plan(project)
                for ms in milestones:
                    pm.add_milestone(
                        uid,
                        title=ms.title,
                        description=ms.description,
                        order=ms.order,
                        acceptance_criteria=ms.acceptance_criteria,
                    )
        return pm.activate(uid)

    def cancel_project(self, project_id: str) -> Project:
        """Cancel a project."""
        pm = self._ensure_project_manager()
        uid = uuid.UUID(project_id)
        return pm.cancel(uid)

    def list_project_milestones(self, project_id: str) -> list[Any]:
        """Return milestones for a project."""
        pm = self._ensure_project_manager()
        uid = uuid.UUID(project_id)
        project = pm.get(uid)
        return list(project.milestones)

    def complete_milestone(self, project_id: str, milestone_id: str) -> Any:
        """Complete a milestone within a project."""
        pm = self._ensure_project_manager()
        return pm.complete_milestone(
            uuid.UUID(project_id), uuid.UUID(milestone_id)
        )

    def list_project_goals(self, project_id: str) -> list[Goal]:
        """Return all goals linked to a project's milestones."""
        pm = self._ensure_project_manager()
        uid = uuid.UUID(project_id)
        project = pm.get(uid)
        goal_ids: list[uuid.UUID] = []
        for ms in project.milestones:
            goal_ids.extend(ms.goal_ids)
        goals: list[Goal] = []
        for gid in goal_ids:
            try:
                goals.append(self.goal_manager.get(gid))
            except KeyError:
                pass
        return goals

    def add_goal_to_project(
        self,
        project_id: str,
        milestone_id: str,
        *,
        title: str,
        description: str,
        priority: str = "medium",
        constraints: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> Goal:
        """Create a goal and link it to a project milestone."""
        pm = self._ensure_project_manager()
        p_uid = uuid.UUID(project_id)
        m_uid = uuid.UUID(milestone_id)
        project = pm.get(p_uid)

        # Find the milestone
        milestone = None
        for ms in project.milestones:
            if ms.milestone_id == m_uid:
                milestone = ms
                break
        if milestone is None:
            raise KeyError(f"Milestone {milestone_id} not found")

        # Create the goal via GoalManager
        goal = self.create_goal(
            title=title,
            description=description,
            priority=priority,
            constraints=constraints,
            acceptance_criteria=acceptance_criteria,
        )

        # Link it to the milestone
        milestone.goal_ids.append(goal.goal_id)
        return goal

    # ------------------------------------------------------------------
    # Status dashboard
    # ------------------------------------------------------------------

    def get_status_data(self) -> dict[str, Any]:
        """Collect data for the status dashboard."""
        goals = self.goal_manager.list_goals()
        active_goals = sum(
            1 for g in goals if g.status in (GoalStatus.ACTIVE, GoalStatus.IN_PROGRESS)
        )

        runs = self.orchestrator.list_runs()
        in_progress = sum(
            1 for r in runs if r.status == PipelineStatus.IN_PROGRESS
        )
        pending_approvals = sum(
            1 for r in runs if r.status == PipelineStatus.BLOCKED
        )

        profiles = self.trust_tracker.profiles
        active_claims = self.claim_manager.get_active_claims()
        queue_entries = self.deploy_queue.list_queue()

        return {
            "active_goals": active_goals,
            "pipeline_runs_in_progress": in_progress,
            "pending_approvals": pending_approvals,
            "agent_count": len(profiles),
            "active_agents": len(active_claims),
            "deploy_queue_length": len(queue_entries),
        }

    # ------------------------------------------------------------------
    # Approve / reject
    # ------------------------------------------------------------------

    def approve_run(self, run_id: str, comment: str | None = None) -> PipelineRun:
        """Approve a blocked pipeline run.

        When the run has worktree metadata (``worktree_path`` and
        ``branch_name``), the task branch is committed and merged into
        the project's default branch before the worktree is cleaned up.
        """
        uid = uuid.UUID(run_id)
        run = self.orchestrator.get_run(uid)
        if run is None:
            raise KeyError(f"Pipeline run {run_id} not found")
        if run.status != PipelineStatus.BLOCKED:
            raise ValueError(
                f"Run {run_id} is not pending approval "
                f"(status: {run.status.value})"
            )
        run.mark_completed(PipelineStatus.PASSED)
        if comment:
            run.metadata["approval_comment"] = comment

        # Merge worktree branch if present
        self._merge_worktree_on_approval(run)

        # Record a successful deployment for the agent
        self.trust_tracker.record_outcome(
            run.agent_id, success=True, risk_score=0.5
        )

        # Release lease if present
        task_id_str = run.metadata.get("task_id")
        if task_id_str and self.lease_manager is not None:
            try:
                tid = uuid.UUID(task_id_str)
                self.lease_manager.release(tid, run.agent_id)
            except Exception:
                pass

        # Mark the task as completed
        if task_id_str:
            try:
                from src.goals.models import TaskStatus
                self.goal_manager.update_task_status(
                    uuid.UUID(task_id_str), TaskStatus.COMPLETED
                )
            except Exception:
                pass

        # Persist the updated run
        self.orchestrator._save_run(run)
        return run

    def _merge_worktree_on_approval(self, run: PipelineRun) -> None:
        """Commit, merge, and clean up the worktree for an approved run."""
        if self.worktree_manager is None:
            return
        worktree_path = run.metadata.get("worktree_path")
        branch_name = run.metadata.get("branch_name")
        if not (worktree_path and branch_name):
            return

        import logging
        logger = logging.getLogger(__name__)

        # Commit any uncommitted changes
        try:
            desc = run.metadata.get("description", "Approved change")
            self.worktree_manager.commit(worktree_path, f"Approved: {desc}")
        except Exception:
            logger.debug("Commit on approval failed (may already be committed)")

        # Find the project to get repo_dir
        task_id_str = run.metadata.get("task_id")
        project = None
        task_obj = None
        if task_id_str and self.project_manager:
            try:
                tid = uuid.UUID(task_id_str)
                task_obj = self._find_task(tid)
                if task_obj:
                    project = self._find_project_for_goal(task_obj.goal_id)
            except Exception:
                pass

        if project and task_obj:
            # Ensure task has the branch_name for merge
            task_obj.branch_name = branch_name
            try:
                repo_dir = (
                    project.repo_local_path
                    or str(
                        self.worktree_manager.repos_dir
                        / str(project.project_id)
                    )
                )
                # Clean up worktree first (git requires this before merge)
                self.worktree_manager.cleanup(worktree_path, repo_dir)
                # Merge
                merged = self.worktree_manager.merge(project, task_obj)
                run.metadata["merged"] = merged
                if merged:
                    logger.info(
                        "Merged branch %s for run %s",
                        branch_name,
                        run.run_id,
                    )
            except Exception:
                logger.exception("Merge on approval failed for run %s", run.run_id)
        else:
            # No project context — just clean up the worktree
            try:
                self.worktree_manager.cleanup(worktree_path)
            except Exception:
                pass

    def _find_task(self, task_id: uuid.UUID):
        """Find a task by ID across all goals."""
        for goal in self.goal_manager.list_goals():
            for task in self.goal_manager.get_tasks(goal.goal_id):
                if task.task_id == task_id:
                    return task
        return None

    def _find_project_for_goal(self, goal_id: uuid.UUID):
        """Find the project that owns a goal."""
        if self.project_manager is None:
            return None
        for project in self.project_manager.list_projects():
            for ms in project.milestones:
                if goal_id in ms.goal_ids:
                    return project
        return None

    def reject_run(self, run_id: str, reason: str) -> PipelineRun:
        """Reject a blocked pipeline run with structured feedback."""
        uid = uuid.UUID(run_id)
        run = self.orchestrator.get_run(uid)
        if run is None:
            raise KeyError(f"Pipeline run {run_id} not found")
        if run.status != PipelineStatus.BLOCKED:
            raise ValueError(f"Run {run_id} is not pending approval (status: {run.status.value})")
        run.mark_completed(PipelineStatus.FAILED)
        run.metadata["rejection_reason"] = reason
        # Persist the updated run
        self.orchestrator._save_run(run)
        return run

    # ------------------------------------------------------------------
    # Agent queries
    # ------------------------------------------------------------------

    def list_agents(self) -> list[AgentProfile]:
        """Return all known agent profiles."""
        return list(self.trust_tracker.profiles.values())

    def get_agent(self, agent_id: str) -> AgentProfile:
        """Return a single agent profile."""
        return self.trust_tracker.get_profile(agent_id)

    # ------------------------------------------------------------------
    # Pipeline run queries
    # ------------------------------------------------------------------

    def list_runs(
        self,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[PipelineRun]:
        """List recent pipeline runs with optional filters."""
        runs = self.orchestrator.list_runs(agent_id=agent_id)
        if status:
            status_enum = PipelineStatus(status)
            runs = [r for r in runs if r.status == status_enum]
        # Sort by start time descending, then limit
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def register_agent(
        self,
        *,
        agent_id: str,
        name: str,
        capabilities: list[str],
        languages: list[str] | None = None,
        frameworks: list[str] | None = None,
        max_concurrent_tasks: int = 1,
    ) -> RoutingAgentRegistration:
        """Register an agent with the routing system."""
        if self.agent_registry is None:
            raise RuntimeError("Routing not initialised")
        caps = [_capability_from_string(c) for c in capabilities]
        primary = caps[0] if caps else AgentCapability.GENERIC
        registration = RoutingAgentRegistration(
            agent_id=agent_id,
            name=name,
            capabilities=caps,
            primary_capability=primary,
            languages=languages or [],
            frameworks=frameworks or [],
            max_concurrent_tasks=max_concurrent_tasks,
        )
        self.agent_registry.register(registration)
        # Ensure a trust profile exists for this agent.
        self.trust_tracker.get_profile(agent_id)
        return registration

    def list_registered_agents(self) -> list[RoutingAgentRegistration]:
        """Return all agents registered in the routing system."""
        if self.agent_registry is None:
            return []
        return self.agent_registry.list_agents()

    def route_task(self, task_id: str) -> RouteDecision:
        """Route a single task by ID."""
        if self.task_router is None:
            raise RuntimeError("Routing not initialised")
        uid = uuid.UUID(task_id)
        # Find the task across all goals.
        for goal in self.goal_manager.list_goals():
            for task in self.goal_manager.get_tasks(goal.goal_id):
                if task.task_id == uid:
                    return self.task_router.route(task)
        raise KeyError(f"Task {task_id} not found")

    def auto_route_goal(self, goal_id: str) -> list[RouteDecision]:
        """Auto-route all ready tasks for a goal."""
        if self.routing_bridge is None:
            raise RuntimeError("Routing not initialised")
        uid = uuid.UUID(goal_id)
        return self.routing_bridge.auto_route_goal(
            uid, self.goal_manager, self.orchestrator
        )

    # ------------------------------------------------------------------
    # Deploy queue
    # ------------------------------------------------------------------

    def list_queue(self) -> list[Any]:
        """Return ordered deploy queue entries."""
        return self.deploy_queue.list_queue()
