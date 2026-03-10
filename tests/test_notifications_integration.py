"""Integration tests: notification events fired by pipeline and goal manager."""

from __future__ import annotations

from src.cli.runtime import CLIRuntime
from src.coordination.claims import ClaimManager
from src.coordination.queue import DeployQueue
from src.goals.manager import GoalManager
from src.goals.models import GoalInput, GoalPriority, TaskStatus
from src.intent.registry import IntentRegistry
from src.intent.schema import IntentDeclaration
from src.notifications.dispatcher import EventDispatcher
from src.notifications.models import Event, EventType
from src.pipeline.models import PipelineConfig, PipelineStatus
from src.pipeline.orchestrator import PipelineOrchestrator
from src.sandbox.manager import SandboxManager
from src.trust.scorer import RiskScorer
from src.trust.tracker import TrustTracker
from src.validation.gate import ValidationGate
from src.validation.signals import (
    BehavioralDiffRunner,
    IntentAlignmentRunner,
    ResourceBoundsRunner,
    SecurityScanRunner,
    StaticAnalysisRunner,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TrackingDispatcher(EventDispatcher):
    """EventDispatcher subclass that records every dispatched event."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[Event] = []

    def dispatch(self, event: Event) -> list:
        self.events.append(event)
        return super().dispatch(event)


def _make_intent(agent_id: str = "agent-1") -> IntentDeclaration:
    return IntentDeclaration(
        agent_id=agent_id,
        description="Add caching to user service",
        rationale="Improve response time",
        target_files=["src/app/cache.py"],
    )


def _make_orchestrator(
    *,
    event_dispatcher: EventDispatcher | None = None,
    force_validation_pass: bool = True,
) -> PipelineOrchestrator:
    runners = [
        StaticAnalysisRunner(force_pass=force_validation_pass),
        BehavioralDiffRunner(force_pass=force_validation_pass),
        IntentAlignmentRunner(force_pass=force_validation_pass),
        ResourceBoundsRunner(force_pass=force_validation_pass),
        SecurityScanRunner(force_pass=force_validation_pass),
    ]
    return PipelineOrchestrator(
        intent_registry=IntentRegistry(constraints=[]),
        sandbox_manager=SandboxManager(),
        validation_gate=ValidationGate(runners),
        risk_scorer=RiskScorer(),
        trust_tracker=TrustTracker(),
        claim_manager=ClaimManager(),
        deploy_queue=DeployQueue(),
        config=PipelineConfig(),
        event_dispatcher=event_dispatcher,
    )




# ---------------------------------------------------------------------------
# Pipeline orchestrator events
# ---------------------------------------------------------------------------


class TestPipelineDispatchEvents:
    """PipelineOrchestrator fires notification events at key pipeline points."""

    def test_pipeline_started_and_passed_events(self) -> None:
        dispatcher = TrackingDispatcher()
        orchestrator = _make_orchestrator(event_dispatcher=dispatcher)

        result = orchestrator.run(_make_intent(), "agent-1")

        events = dispatcher.events
        types = [e.event_type for e in events]

        assert EventType.PIPELINE_STARTED in types
        assert EventType.PIPELINE_PASSED in types or EventType.APPROVAL_NEEDED in types

        # Check PIPELINE_STARTED data
        started = [e for e in events if e.event_type == EventType.PIPELINE_STARTED][0]
        assert started.data["run_id"] == str(result.run_id)
        assert started.data["agent_id"] == "agent-1"

    def test_pipeline_failed_event_on_validation_failure(self) -> None:
        dispatcher = TrackingDispatcher()
        orchestrator = _make_orchestrator(
            event_dispatcher=dispatcher,
            force_validation_pass=False,
        )

        result = orchestrator.run(_make_intent(), "agent-1")

        events = dispatcher.events
        types = [e.event_type for e in events]

        assert EventType.PIPELINE_STARTED in types
        assert EventType.PIPELINE_FAILED in types

        failed = [e for e in events if e.event_type == EventType.PIPELINE_FAILED][0]
        assert failed.data["run_id"] == str(result.run_id)
        assert failed.data["agent_id"] == "agent-1"
        assert "stage" in failed.data

    def test_no_dispatcher_still_works(self) -> None:
        """Pipeline runs fine without an event dispatcher (backward compat)."""
        orchestrator = _make_orchestrator(event_dispatcher=None)
        result = orchestrator.run(_make_intent(), "agent-1")
        assert result.status in (PipelineStatus.PASSED, PipelineStatus.BLOCKED)


# ---------------------------------------------------------------------------
# Goal manager events
# ---------------------------------------------------------------------------


class TestGoalManagerDispatchEvents:
    """GoalManager fires notification events on goal lifecycle changes."""

    def test_goal_created_event(self) -> None:
        dispatcher = TrackingDispatcher()
        manager = GoalManager(event_dispatcher=dispatcher)

        goal = manager.create(
            GoalInput(
                title="Test goal",
                description="A test",
                priority=GoalPriority.HIGH,
            ),
            created_by="cli-user",
        )

        events = dispatcher.events
        types = [e.event_type for e in events]
        assert EventType.GOAL_CREATED in types

        created = [e for e in events if e.event_type == EventType.GOAL_CREATED][0]
        assert created.data["goal_id"] == str(goal.goal_id)
        assert created.data["title"] == "Test goal"
        assert created.data["created_by"] == "cli-user"

    def test_goal_activated_event(self) -> None:
        dispatcher = TrackingDispatcher()
        manager = GoalManager(event_dispatcher=dispatcher)

        goal = manager.create(
            GoalInput(title="Test", description="A test"),
            created_by="cli-user",
        )
        breakdown = manager.activate(goal.goal_id)

        events = dispatcher.events
        types = [e.event_type for e in events]
        assert EventType.GOAL_ACTIVATED in types

        activated = [e for e in events if e.event_type == EventType.GOAL_ACTIVATED][0]
        assert activated.data["goal_id"] == str(goal.goal_id)
        assert activated.data["task_count"] == len(breakdown.tasks)

    def test_goal_completed_event_on_auto_complete(self) -> None:
        dispatcher = TrackingDispatcher()
        manager = GoalManager(event_dispatcher=dispatcher)

        goal = manager.create(
            GoalInput(title="Test", description="A test"),
            created_by="cli-user",
        )
        breakdown = manager.activate(goal.goal_id)

        # Complete all tasks to trigger auto-complete
        for task in breakdown.tasks:
            manager.update_task_status(task.task_id, TaskStatus.COMPLETED)

        events = dispatcher.events
        types = [e.event_type for e in events]
        assert EventType.GOAL_COMPLETED in types

        completed = [e for e in events if e.event_type == EventType.GOAL_COMPLETED][0]
        assert completed.data["goal_id"] == str(goal.goal_id)

    def test_no_dispatcher_still_works(self) -> None:
        """GoalManager works fine without an event dispatcher (backward compat)."""
        manager = GoalManager()
        goal = manager.create(
            GoalInput(title="Test", description="A test"),
            created_by="cli-user",
        )
        manager.activate(goal.goal_id)
        assert goal.title == "Test"


# ---------------------------------------------------------------------------
# CLIRuntime wiring
# ---------------------------------------------------------------------------


class TestCLIRuntimeDispatcher:
    """CLIRuntime.from_defaults() creates and wires an EventDispatcher."""

    def test_from_defaults_creates_dispatcher(self) -> None:
        runtime = CLIRuntime.from_defaults()
        assert runtime.event_dispatcher is not None
        assert isinstance(runtime.event_dispatcher, EventDispatcher)

    def test_from_defaults_shares_dispatcher(self) -> None:
        """Orchestrator and goal manager share the same dispatcher instance."""
        runtime = CLIRuntime.from_defaults()
        assert runtime.orchestrator._event_dispatcher is runtime.event_dispatcher
        assert runtime.goal_manager._event_dispatcher is runtime.event_dispatcher
