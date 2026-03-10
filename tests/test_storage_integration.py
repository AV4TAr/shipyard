"""Integration tests verifying storage repositories are properly wired into managers."""

from __future__ import annotations

import uuid

import pytest

from src.cli.runtime import CLIRuntime
from src.goals.decomposer import GoalDecomposer
from src.goals.manager import GoalManager
from src.goals.models import GoalInput, GoalStatus, TaskStatus
from src.intent.registry import IntentRegistry
from src.intent.schema import IntentDeclaration
from src.storage.factory import create_storage
from src.storage.memory import (
    MemoryAgentProfileRepository,
    MemoryGoalRepository,
    MemoryIntentRepository,
    MemoryTaskRepository,
)
from src.trust.tracker import TrustTracker

# ---------------------------------------------------------------------------
# GoalManager with memory repos
# ---------------------------------------------------------------------------


class TestGoalManagerWithMemoryRepos:
    """GoalManager backed by in-memory repositories behaves identically."""

    def _make_manager(self) -> GoalManager:
        return GoalManager(
            decomposer=GoalDecomposer(),
            goal_repo=MemoryGoalRepository(),
            task_repo=MemoryTaskRepository(),
        )

    def _make_input(self, title: str = "Test goal") -> GoalInput:
        return GoalInput(title=title, description="A test goal")

    def test_create_and_get(self):
        mgr = self._make_manager()
        goal = mgr.create(self._make_input(), created_by="tester")
        fetched = mgr.get(goal.goal_id)
        assert fetched.goal_id == goal.goal_id
        assert fetched.title == "Test goal"

    def test_list_goals(self):
        mgr = self._make_manager()
        mgr.create(self._make_input("A"), created_by="t")
        mgr.create(self._make_input("B"), created_by="t")
        assert len(mgr.list_goals()) == 2

    def test_list_goals_filter_status(self):
        mgr = self._make_manager()
        mgr.create(self._make_input("A"), created_by="t")
        assert len(mgr.list_goals(status=GoalStatus.DRAFT)) == 1
        assert len(mgr.list_goals(status=GoalStatus.ACTIVE)) == 0

    def test_activate_and_get_tasks(self):
        mgr = self._make_manager()
        goal = mgr.create(self._make_input(), created_by="t")
        breakdown = mgr.activate(goal.goal_id)
        tasks = mgr.get_tasks(goal.goal_id)
        assert len(tasks) == len(breakdown.tasks)
        assert mgr.get(goal.goal_id).status == GoalStatus.ACTIVE

    def test_cancel(self):
        mgr = self._make_manager()
        goal = mgr.create(self._make_input(), created_by="t")
        mgr.activate(goal.goal_id)
        cancelled = mgr.cancel(goal.goal_id)
        assert cancelled.status == GoalStatus.CANCELLED

    def test_update_task_status(self):
        mgr = self._make_manager()
        goal = mgr.create(self._make_input(), created_by="t")
        breakdown = mgr.activate(goal.goal_id)
        task = breakdown.tasks[0]
        updated = mgr.update_task_status(task.task_id, TaskStatus.IN_PROGRESS)
        assert updated.status == TaskStatus.IN_PROGRESS

    def test_auto_complete(self):
        mgr = self._make_manager()
        goal = mgr.create(self._make_input(), created_by="t")
        breakdown = mgr.activate(goal.goal_id)
        for task in breakdown.tasks:
            mgr.update_task_status(task.task_id, TaskStatus.COMPLETED)
        assert mgr.get(goal.goal_id).status == GoalStatus.COMPLETED

    def test_get_nonexistent_raises(self):
        mgr = self._make_manager()
        with pytest.raises(KeyError):
            mgr.get(uuid.uuid4())


class TestGoalManagerWithoutRepos:
    """GoalManager without repos (backward compat) still works."""

    def test_create_and_get(self):
        mgr = GoalManager()
        inp = GoalInput(title="No repo", description="test")
        goal = mgr.create(inp, created_by="t")
        assert mgr.get(goal.goal_id).title == "No repo"


# ---------------------------------------------------------------------------
# TrustTracker with memory repos
# ---------------------------------------------------------------------------


class TestTrustTrackerWithMemoryRepo:
    def test_get_profile_creates_default(self):
        tracker = TrustTracker(profile_repo=MemoryAgentProfileRepository())
        profile = tracker.get_profile("agent-1")
        assert profile.agent_id == "agent-1"
        assert profile.total_deployments == 0

    def test_record_outcome(self):
        tracker = TrustTracker(profile_repo=MemoryAgentProfileRepository())
        updated = tracker.record_outcome("agent-1", success=True, risk_score=0.3)
        assert updated.total_deployments == 1
        assert updated.successful_deployments == 1

    def test_profiles_property(self):
        tracker = TrustTracker(profile_repo=MemoryAgentProfileRepository())
        tracker.get_profile("a1")
        tracker.get_profile("a2")
        assert len(tracker.profiles) == 2
        assert "a1" in tracker.profiles

    def test_without_repo(self):
        tracker = TrustTracker()
        profile = tracker.get_profile("agent-x")
        assert profile.agent_id == "agent-x"


# ---------------------------------------------------------------------------
# IntentRegistry with memory repos
# ---------------------------------------------------------------------------


class TestIntentRegistryWithMemoryRepo:
    def test_register_saves_to_repo(self):
        repo = MemoryIntentRepository()
        registry = IntentRegistry(intent_repo=repo)
        intent = IntentDeclaration(
            agent_id="agent-1",
            description="Test intent",
            rationale="Testing",
            target_files=["src/foo.py"],
        )
        verdict = registry.register(intent)
        assert verdict.approved
        assert repo.get(intent.intent_id) is not None

    def test_without_repo(self):
        registry = IntentRegistry()
        intent = IntentDeclaration(
            agent_id="agent-1",
            description="Test intent",
            rationale="Testing",
            target_files=["src/foo.py"],
        )
        verdict = registry.register(intent)
        assert verdict.approved
        assert len(registry.get_active()) == 1


# ---------------------------------------------------------------------------
# CLIRuntime.from_defaults
# ---------------------------------------------------------------------------


class TestCLIRuntimeFromDefaults:
    def test_memory_backend(self):
        runtime = CLIRuntime.from_defaults(storage_backend="memory")
        assert runtime.goal_manager is not None
        assert runtime.trust_tracker is not None
        assert runtime.orchestrator is not None

    def test_sqlite_backend(self, tmp_path):
        db = str(tmp_path / "test.db")
        runtime = CLIRuntime.from_defaults(storage_backend="sqlite", db_path=db)
        # Create a goal and verify it works end-to-end
        goal = runtime.create_goal(
            title="Persist me",
            description="Testing sqlite backend",
        )
        fetched, _ = runtime.show_goal(str(goal.goal_id))
        assert fetched.title == "Persist me"

    def test_env_var_triggers_sqlite(self, monkeypatch, tmp_path):
        db = str(tmp_path / "env.db")
        monkeypatch.setenv("AI_CICD_DB_PATH", db)
        runtime = CLIRuntime.from_defaults()
        # Should not raise — sqlite backend is used
        goal = runtime.create_goal(title="Env goal", description="from env")
        assert runtime.list_goals() == [goal]


# ---------------------------------------------------------------------------
# GoalManager with SQLite repos — persistence across instances
# ---------------------------------------------------------------------------


class TestGoalManagerSqlitePersistence:
    def test_data_persists_across_instances(self, tmp_path):
        db = str(tmp_path / "persist.db")

        # Instance 1: create a goal
        storage1 = create_storage(backend="sqlite", db_path=db)
        mgr1 = GoalManager(
            decomposer=GoalDecomposer(),
            goal_repo=storage1.goals,
            task_repo=storage1.tasks,
        )
        goal = mgr1.create(
            GoalInput(title="Persistent", description="test"),
            created_by="tester",
        )
        goal_id = goal.goal_id

        # Instance 2: read back the goal
        storage2 = create_storage(backend="sqlite", db_path=db)
        mgr2 = GoalManager(
            decomposer=GoalDecomposer(),
            goal_repo=storage2.goals,
            task_repo=storage2.tasks,
        )
        fetched = mgr2.get(goal_id)
        assert fetched.title == "Persistent"
        assert fetched.status == GoalStatus.DRAFT
