"""Tests for the Goals System."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.goals.bridge import GoalPipelineBridge
from src.goals.decomposer import GoalDecomposer
from src.goals.manager import GoalManager
from src.goals.models import (
    AgentTask,
    Goal,
    GoalInput,
    GoalPriority,
    GoalStatus,
    TaskBreakdown,
    TaskStatus,
)
from src.intent.schema import IntentDeclaration, RiskLevel
from src.pipeline.models import PipelineRun, PipelineStatus
from src.pipeline.orchestrator import PipelineOrchestrator


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def basic_input() -> GoalInput:
    return GoalInput(
        title="Add caching layer",
        description="Add Redis caching to the user service to reduce DB load",
        constraints=["must not break auth", "use Redis"],
        acceptance_criteria=["cache hit rate > 80%", "latency < 50ms"],
        priority=GoalPriority.HIGH,
        target_services=["user-service"],
    )


@pytest.fixture
def doc_input() -> GoalInput:
    """Goal whose description mentions documentation."""
    return GoalInput(
        title="Add API documentation",
        description="Write documentation for the new REST API endpoints",
        constraints=[],
        acceptance_criteria=["all endpoints documented"],
        priority=GoalPriority.LOW,
        target_services=["api-gateway"],
    )


@pytest.fixture
def manager() -> GoalManager:
    return GoalManager()


@pytest.fixture
def decomposer() -> GoalDecomposer:
    return GoalDecomposer()


# ======================================================================
# Goal creation from minimal GoalInput
# ======================================================================


class TestGoalCreation:
    def test_create_from_input(self, manager: GoalManager, basic_input: GoalInput) -> None:
        goal = manager.create(basic_input, created_by="alice")

        assert goal.title == basic_input.title
        assert goal.description == basic_input.description
        assert goal.constraints == basic_input.constraints
        assert goal.acceptance_criteria == basic_input.acceptance_criteria
        assert goal.priority == GoalPriority.HIGH
        assert goal.target_services == ["user-service"]
        assert goal.created_by == "alice"
        assert goal.status == GoalStatus.DRAFT
        assert goal.goal_id is not None
        assert goal.created_at is not None

    def test_create_auto_generates_id(self, manager: GoalManager, basic_input: GoalInput) -> None:
        g1 = manager.create(basic_input, created_by="bob")
        g2 = manager.create(basic_input, created_by="bob")
        assert g1.goal_id != g2.goal_id

    def test_get_goal(self, manager: GoalManager, basic_input: GoalInput) -> None:
        goal = manager.create(basic_input, created_by="alice")
        retrieved = manager.get(goal.goal_id)
        assert retrieved.goal_id == goal.goal_id

    def test_get_missing_goal_raises(self, manager: GoalManager) -> None:
        with pytest.raises(KeyError):
            manager.get(uuid.uuid4())


# ======================================================================
# Goal lifecycle: draft -> active -> in_progress -> completed
# ======================================================================


class TestGoalLifecycle:
    def test_draft_to_active(self, manager: GoalManager, basic_input: GoalInput) -> None:
        goal = manager.create(basic_input, created_by="alice")
        assert goal.status == GoalStatus.DRAFT

        manager.activate(goal.goal_id)
        assert manager.get(goal.goal_id).status == GoalStatus.ACTIVE

    def test_active_to_in_progress(self, manager: GoalManager, basic_input: GoalInput) -> None:
        goal = manager.create(basic_input, created_by="alice")
        breakdown = manager.activate(goal.goal_id)

        # Moving a task to IN_PROGRESS should move the goal to IN_PROGRESS
        first_task = breakdown.tasks[0]
        manager.update_task_status(first_task.task_id, TaskStatus.IN_PROGRESS)
        assert manager.get(goal.goal_id).status == GoalStatus.IN_PROGRESS

    def test_full_lifecycle(self, manager: GoalManager, basic_input: GoalInput) -> None:
        goal = manager.create(basic_input, created_by="alice")
        assert goal.status == GoalStatus.DRAFT

        breakdown = manager.activate(goal.goal_id)
        assert manager.get(goal.goal_id).status == GoalStatus.ACTIVE

        # Complete all tasks -> goal auto-completes
        for task in breakdown.tasks:
            manager.update_task_status(task.task_id, TaskStatus.COMPLETED)

        assert manager.get(goal.goal_id).status == GoalStatus.COMPLETED


# ======================================================================
# Decomposition produces reasonable tasks
# ======================================================================


class TestDecomposition:
    def test_always_produces_impl_and_test_tasks(
        self, decomposer: GoalDecomposer, basic_input: GoalInput
    ) -> None:
        goal = Goal(
            title=basic_input.title,
            description=basic_input.description,
            constraints=basic_input.constraints,
            target_services=basic_input.target_services,
            created_by="alice",
        )
        breakdown = decomposer.decompose(goal)

        titles = [t.title for t in breakdown.tasks]
        assert any("Implement" in t for t in titles)
        assert any("Test" in t for t in titles)
        assert len(breakdown.tasks) >= 2

    def test_doc_keywords_produce_doc_task(
        self, decomposer: GoalDecomposer, doc_input: GoalInput
    ) -> None:
        goal = Goal(
            title=doc_input.title,
            description=doc_input.description,
            target_services=doc_input.target_services,
            created_by="alice",
        )
        breakdown = decomposer.decompose(goal)

        titles = [t.title for t in breakdown.tasks]
        assert any("Document" in t for t in titles)
        assert len(breakdown.tasks) == 3

    def test_no_doc_task_without_keywords(
        self, decomposer: GoalDecomposer, basic_input: GoalInput
    ) -> None:
        goal = Goal(
            title=basic_input.title,
            description=basic_input.description,
            target_services=basic_input.target_services,
            created_by="alice",
        )
        breakdown = decomposer.decompose(goal)

        titles = [t.title for t in breakdown.tasks]
        assert not any("Document" in t for t in titles)
        assert len(breakdown.tasks) == 2

    def test_breakdown_has_correct_goal_id(
        self, decomposer: GoalDecomposer, basic_input: GoalInput
    ) -> None:
        goal = Goal(
            title=basic_input.title,
            description=basic_input.description,
            created_by="alice",
        )
        breakdown = decomposer.decompose(goal)

        assert breakdown.goal_id == goal.goal_id
        for task in breakdown.tasks:
            assert task.goal_id == goal.goal_id


# ======================================================================
# Task dependencies are set correctly
# ======================================================================


class TestTaskDependencies:
    def test_test_depends_on_impl(
        self, decomposer: GoalDecomposer, basic_input: GoalInput
    ) -> None:
        goal = Goal(
            title=basic_input.title,
            description=basic_input.description,
            created_by="alice",
        )
        breakdown = decomposer.decompose(goal)

        impl_task = next(t for t in breakdown.tasks if "Implement" in t.title)
        test_task = next(t for t in breakdown.tasks if "Test" in t.title)

        assert impl_task.task_id in test_task.depends_on
        assert len(impl_task.depends_on) == 0

    def test_doc_depends_on_impl(
        self, decomposer: GoalDecomposer, doc_input: GoalInput
    ) -> None:
        goal = Goal(
            title=doc_input.title,
            description=doc_input.description,
            created_by="alice",
        )
        breakdown = decomposer.decompose(goal)

        impl_task = next(t for t in breakdown.tasks if "Implement" in t.title)
        doc_task = next(t for t in breakdown.tasks if "Document" in t.title)

        assert impl_task.task_id in doc_task.depends_on


# ======================================================================
# Constraints are inherited from goal to tasks
# ======================================================================


class TestConstraintInheritance:
    def test_impl_task_inherits_constraints(
        self, decomposer: GoalDecomposer
    ) -> None:
        goal = Goal(
            title="Secure endpoint",
            description="Add rate limiting",
            constraints=["must not break auth", "use Redis"],
            created_by="alice",
        )
        breakdown = decomposer.decompose(goal)

        impl_task = next(t for t in breakdown.tasks if "Implement" in t.title)
        assert "must not break auth" in impl_task.constraints
        assert "use Redis" in impl_task.constraints

    def test_test_task_gets_extra_constraint(
        self, decomposer: GoalDecomposer
    ) -> None:
        goal = Goal(
            title="Feature X",
            description="Build feature X",
            constraints=["no downtime"],
            created_by="alice",
        )
        breakdown = decomposer.decompose(goal)

        test_task = next(t for t in breakdown.tasks if "Test" in t.title)
        assert "no downtime" in test_task.constraints
        assert "All new code must have test coverage" in test_task.constraints


# ======================================================================
# Bridge converts tasks to intents correctly
# ======================================================================


class TestBridge:
    def test_task_to_intent(self) -> None:
        task = AgentTask(
            goal_id=uuid.uuid4(),
            title="Implement caching",
            description="Add Redis caching to user service",
            target_files=["src/user/cache.py"],
            target_services=["user-service"],
            constraints=["use Redis"],
            estimated_risk=RiskLevel.MEDIUM,
        )

        orchestrator = MagicMock(spec=PipelineOrchestrator)
        bridge = GoalPipelineBridge(orchestrator)

        intent = bridge.task_to_intent(task, agent_id="agent-1")

        assert isinstance(intent, IntentDeclaration)
        assert intent.agent_id == "agent-1"
        assert intent.description == task.description
        assert intent.target_files == ["src/user/cache.py"]
        assert intent.target_services == ["user-service"]
        assert intent.risk_hints["estimated_risk"] == "medium"
        assert intent.metadata["task_id"] == str(task.task_id)
        assert intent.metadata["goal_id"] == str(task.goal_id)
        assert intent.metadata["constraints"] == ["use Redis"]

    def test_assign_task_calls_pipeline(self) -> None:
        task = AgentTask(
            goal_id=uuid.uuid4(),
            title="Implement caching",
            description="Add Redis caching",
            target_files=["src/cache.py"],
        )

        mock_run = PipelineRun(
            agent_id="agent-1",
            status=PipelineStatus.PASSED,
        )
        orchestrator = MagicMock(spec=PipelineOrchestrator)
        orchestrator.run.return_value = mock_run

        bridge = GoalPipelineBridge(orchestrator)
        result = bridge.assign_task(task, agent_id="agent-1")

        assert task.status == TaskStatus.ASSIGNED
        orchestrator.run.assert_called_once()
        assert result.status == PipelineStatus.PASSED

    def test_report_result_passed(self) -> None:
        task = AgentTask(
            goal_id=uuid.uuid4(),
            title="Task",
            description="Do the thing",
            target_files=[],
        )
        run = PipelineRun(agent_id="agent-1", status=PipelineStatus.PASSED)

        orchestrator = MagicMock(spec=PipelineOrchestrator)
        bridge = GoalPipelineBridge(orchestrator)
        bridge.report_result(task, run)

        assert task.status == TaskStatus.COMPLETED

    def test_report_result_failed(self) -> None:
        task = AgentTask(
            goal_id=uuid.uuid4(),
            title="Task",
            description="Do the thing",
            target_files=[],
        )
        run = PipelineRun(agent_id="agent-1", status=PipelineStatus.FAILED)

        orchestrator = MagicMock(spec=PipelineOrchestrator)
        bridge = GoalPipelineBridge(orchestrator)
        bridge.report_result(task, run)

        assert task.status == TaskStatus.FAILED

    def test_report_result_blocked(self) -> None:
        task = AgentTask(
            goal_id=uuid.uuid4(),
            title="Task",
            description="Do the thing",
            target_files=[],
        )
        run = PipelineRun(agent_id="agent-1", status=PipelineStatus.BLOCKED)

        orchestrator = MagicMock(spec=PipelineOrchestrator)
        bridge = GoalPipelineBridge(orchestrator)
        bridge.report_result(task, run)

        assert task.status == TaskStatus.IN_PROGRESS


# ======================================================================
# GoalManager auto-completes when all tasks done
# ======================================================================


class TestAutoComplete:
    def test_auto_complete_on_all_tasks_done(
        self, manager: GoalManager, basic_input: GoalInput
    ) -> None:
        goal = manager.create(basic_input, created_by="alice")
        breakdown = manager.activate(goal.goal_id)

        # Complete all tasks one by one
        for task in breakdown.tasks:
            manager.update_task_status(task.task_id, TaskStatus.COMPLETED)

        assert manager.get(goal.goal_id).status == GoalStatus.COMPLETED

    def test_not_auto_complete_with_pending_tasks(
        self, manager: GoalManager, basic_input: GoalInput
    ) -> None:
        goal = manager.create(basic_input, created_by="alice")
        breakdown = manager.activate(goal.goal_id)

        # Complete only the first task
        manager.update_task_status(breakdown.tasks[0].task_id, TaskStatus.COMPLETED)

        assert manager.get(goal.goal_id).status != GoalStatus.COMPLETED

    def test_update_nonexistent_task_raises(self, manager: GoalManager) -> None:
        with pytest.raises(KeyError):
            manager.update_task_status(uuid.uuid4(), TaskStatus.COMPLETED)


# ======================================================================
# Cancellation works
# ======================================================================


class TestCancellation:
    def test_cancel_goal(self, manager: GoalManager, basic_input: GoalInput) -> None:
        goal = manager.create(basic_input, created_by="alice")
        manager.activate(goal.goal_id)

        manager.cancel(goal.goal_id)
        assert manager.get(goal.goal_id).status == GoalStatus.CANCELLED

    def test_cancel_marks_pending_tasks_failed(
        self, manager: GoalManager, basic_input: GoalInput
    ) -> None:
        goal = manager.create(basic_input, created_by="alice")
        breakdown = manager.activate(goal.goal_id)

        manager.cancel(goal.goal_id)

        for task in manager.get_tasks(goal.goal_id):
            assert task.status == TaskStatus.FAILED

    def test_cancel_nonexistent_goal_raises(self, manager: GoalManager) -> None:
        with pytest.raises(KeyError):
            manager.cancel(uuid.uuid4())

    def test_cancelled_goal_not_auto_completed(
        self, manager: GoalManager, basic_input: GoalInput
    ) -> None:
        """Even if we somehow complete all tasks after cancellation, goal stays cancelled."""
        goal = manager.create(basic_input, created_by="alice")
        breakdown = manager.activate(goal.goal_id)
        manager.cancel(goal.goal_id)

        # Force-complete all tasks (they are FAILED, but update them)
        for task in breakdown.tasks:
            manager.update_task_status(task.task_id, TaskStatus.COMPLETED)

        assert manager.get(goal.goal_id).status == GoalStatus.CANCELLED


# ======================================================================
# Filtering by status / priority
# ======================================================================


class TestFiltering:
    def test_filter_by_status(self, manager: GoalManager) -> None:
        inp1 = GoalInput(title="A", description="a", priority=GoalPriority.LOW)
        inp2 = GoalInput(title="B", description="b", priority=GoalPriority.HIGH)

        g1 = manager.create(inp1, created_by="alice")
        g2 = manager.create(inp2, created_by="bob")
        manager.activate(g2.goal_id)

        drafts = manager.list_goals(status=GoalStatus.DRAFT)
        assert len(drafts) == 1
        assert drafts[0].goal_id == g1.goal_id

        actives = manager.list_goals(status=GoalStatus.ACTIVE)
        assert len(actives) == 1
        assert actives[0].goal_id == g2.goal_id

    def test_filter_by_priority(self, manager: GoalManager) -> None:
        inp1 = GoalInput(title="A", description="a", priority=GoalPriority.LOW)
        inp2 = GoalInput(title="B", description="b", priority=GoalPriority.HIGH)
        inp3 = GoalInput(title="C", description="c", priority=GoalPriority.HIGH)

        manager.create(inp1, created_by="alice")
        manager.create(inp2, created_by="bob")
        manager.create(inp3, created_by="carol")

        high = manager.list_goals(priority=GoalPriority.HIGH)
        assert len(high) == 2

        low = manager.list_goals(priority=GoalPriority.LOW)
        assert len(low) == 1

    def test_list_all(self, manager: GoalManager) -> None:
        inp1 = GoalInput(title="A", description="a")
        inp2 = GoalInput(title="B", description="b")

        manager.create(inp1, created_by="alice")
        manager.create(inp2, created_by="bob")

        assert len(manager.list_goals()) == 2

    def test_list_empty(self, manager: GoalManager) -> None:
        assert manager.list_goals() == []


# ======================================================================
# Risk estimation
# ======================================================================


class TestRiskEstimation:
    def test_auth_related_is_critical(self, decomposer: GoalDecomposer) -> None:
        task = AgentTask(
            goal_id=uuid.uuid4(),
            title="Fix auth",
            description="Update authentication flow",
            target_files=["src/auth/handler.py"],
        )
        assert decomposer.estimate_risk(task) == RiskLevel.CRITICAL

    def test_database_is_high(self, decomposer: GoalDecomposer) -> None:
        task = AgentTask(
            goal_id=uuid.uuid4(),
            title="DB migration",
            description="Add new database table",
            target_files=["migrations/001.sql"],
        )
        assert decomposer.estimate_risk(task) == RiskLevel.HIGH

    def test_simple_task_is_low(self, decomposer: GoalDecomposer) -> None:
        task = AgentTask(
            goal_id=uuid.uuid4(),
            title="Update readme",
            description="Fix typo in help text",
            target_files=["src/ui/help.py"],
        )
        assert decomposer.estimate_risk(task) == RiskLevel.LOW

    def test_multi_service_is_medium(self, decomposer: GoalDecomposer) -> None:
        task = AgentTask(
            goal_id=uuid.uuid4(),
            title="Cross-service refactor",
            description="Rename shared types",
            target_files=[],
            target_services=["svc-a", "svc-b"],
        )
        assert decomposer.estimate_risk(task) == RiskLevel.MEDIUM


# ======================================================================
# Goal.get_tasks with no activation
# ======================================================================


class TestGetTasks:
    def test_get_tasks_before_activation(
        self, manager: GoalManager, basic_input: GoalInput
    ) -> None:
        goal = manager.create(basic_input, created_by="alice")
        assert manager.get_tasks(goal.goal_id) == []

    def test_get_tasks_after_activation(
        self, manager: GoalManager, basic_input: GoalInput
    ) -> None:
        goal = manager.create(basic_input, created_by="alice")
        manager.activate(goal.goal_id)
        tasks = manager.get_tasks(goal.goal_id)
        assert len(tasks) >= 2
