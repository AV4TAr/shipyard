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
from src.sandbox.manager import SandboxManager
from src.trust.models import AgentProfile
from src.trust.scorer import RiskScorer
from src.trust.tracker import TrustTracker
from src.validation.gate import ValidationGate


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
    ) -> None:
        self.goal_manager = goal_manager
        self.orchestrator = orchestrator
        self.trust_tracker = trust_tracker
        self.claim_manager = claim_manager
        self.deploy_queue = deploy_queue
        self.intent_registry = intent_registry

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
        SQLite.  The default ``"memory"`` backend keeps everything in-memory
        as before.

        The environment variable ``AI_CICD_DB_PATH`` can also be used to
        enable SQLite persistence without passing arguments explicitly.

        Useful for local development, demos, and tests.
        """
        # Allow env-var to override the storage backend
        env_db_path = os.environ.get("AI_CICD_DB_PATH")
        if env_db_path:
            storage_backend = "sqlite"
            db_path = db_path or env_db_path

        from src.storage.factory import create_storage

        storage = create_storage(backend=storage_backend, db_path=db_path)

        intent_registry = IntentRegistry(intent_repo=storage.intents)

        sandbox_backend = None
        if os.environ.get("OPENSANDBOX_SERVER_URL"):
            from src.sandbox.backends import OpenSandboxBackend

            sandbox_backend = OpenSandboxBackend()
        sandbox_manager = SandboxManager(backend=sandbox_backend)

        validation_gate = ValidationGate(runners=[])
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
        )

        goal_manager = GoalManager(
            decomposer=GoalDecomposer(),
            goal_repo=storage.goals,
            task_repo=storage.tasks,
        )

        return cls(
            goal_manager=goal_manager,
            orchestrator=orchestrator,
            trust_tracker=trust_tracker,
            claim_manager=claim_manager,
            deploy_queue=deploy_queue,
            intent_registry=intent_registry,
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
        """Approve a blocked pipeline run."""
        uid = uuid.UUID(run_id)
        run = self.orchestrator.get_run(uid)
        if run is None:
            raise KeyError(f"Pipeline run {run_id} not found")
        if run.status != PipelineStatus.BLOCKED:
            raise ValueError(f"Run {run_id} is not pending approval (status: {run.status.value})")
        run.mark_completed(PipelineStatus.PASSED)
        if comment:
            run.metadata["approval_comment"] = comment
        # Record a successful deployment for the agent
        self.trust_tracker.record_outcome(
            run.agent_id, success=True, risk_score=0.5
        )
        return run

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
    # Deploy queue
    # ------------------------------------------------------------------

    def list_queue(self) -> list[Any]:
        """Return ordered deploy queue entries."""
        return self.deploy_queue.list_queue()
