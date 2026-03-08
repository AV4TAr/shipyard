"""Tests for the storage layer (memory and SQLite backends)."""

from __future__ import annotations

import uuid

import pytest

from src.goals.models import AgentTask, Goal, GoalPriority, GoalStatus
from src.intent.schema import IntentDeclaration
from src.pipeline.models import PipelineRun, PipelineStatus
from src.storage import StorageBackend, create_storage
from src.trust.models import AgentProfile

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def memory_backend() -> StorageBackend:
    return create_storage("memory")


@pytest.fixture()
def sqlite_backend(tmp_path) -> StorageBackend:
    return create_storage("sqlite", db_path=str(tmp_path / "test.db"))


@pytest.fixture(params=["memory", "sqlite"])
def backend(request, tmp_path) -> StorageBackend:
    """Parametrised fixture that yields both memory and sqlite backends."""
    if request.param == "memory":
        return create_storage("memory")
    return create_storage("sqlite", db_path=str(tmp_path / "test.db"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_goal(**overrides) -> Goal:
    defaults = {"title": "Test goal", "description": "A test goal"}
    defaults.update(overrides)
    return Goal(**defaults)


def _make_task(goal_id: uuid.UUID, **overrides) -> AgentTask:
    defaults = {"goal_id": goal_id, "title": "Test task", "description": "A test task"}
    defaults.update(overrides)
    return AgentTask(**defaults)


def _make_pipeline_run(**overrides) -> PipelineRun:
    defaults: dict = {}
    defaults.update(overrides)
    return PipelineRun(**defaults)


def _make_agent_profile(agent_id: str = "agent-1", **overrides) -> AgentProfile:
    defaults: dict = {"agent_id": agent_id}
    defaults.update(overrides)
    return AgentProfile(**defaults)


def _make_intent(agent_id: str = "agent-1", **overrides) -> IntentDeclaration:
    defaults: dict = {
        "agent_id": agent_id,
        "description": "Change auth module",
        "rationale": "Security fix",
        "target_files": ["src/auth.py"],
    }
    defaults.update(overrides)
    return IntentDeclaration(**defaults)


# ---------------------------------------------------------------------------
# Goal repository tests
# ---------------------------------------------------------------------------


class TestGoalRepository:
    def test_save_and_get(self, backend: StorageBackend) -> None:
        goal = _make_goal()
        backend.goals.save(goal)
        retrieved = backend.goals.get(goal.goal_id)
        assert retrieved is not None
        assert retrieved.goal_id == goal.goal_id
        assert retrieved.title == goal.title

    def test_get_nonexistent(self, backend: StorageBackend) -> None:
        assert backend.goals.get(uuid.uuid4()) is None

    def test_list_all(self, backend: StorageBackend) -> None:
        g1 = _make_goal(title="Goal 1")
        g2 = _make_goal(title="Goal 2")
        backend.goals.save(g1)
        backend.goals.save(g2)
        all_goals = backend.goals.list_all()
        assert len(all_goals) == 2

    def test_list_all_filter_status(self, backend: StorageBackend) -> None:
        g1 = _make_goal(status=GoalStatus.DRAFT)
        g2 = _make_goal(status=GoalStatus.ACTIVE)
        backend.goals.save(g1)
        backend.goals.save(g2)
        drafts = backend.goals.list_all(status=GoalStatus.DRAFT)
        assert len(drafts) == 1
        assert drafts[0].goal_id == g1.goal_id

    def test_list_all_filter_priority(self, backend: StorageBackend) -> None:
        g1 = _make_goal(priority=GoalPriority.HIGH)
        g2 = _make_goal(priority=GoalPriority.LOW)
        backend.goals.save(g1)
        backend.goals.save(g2)
        high = backend.goals.list_all(priority=GoalPriority.HIGH)
        assert len(high) == 1
        assert high[0].goal_id == g1.goal_id

    def test_list_all_filter_both(self, backend: StorageBackend) -> None:
        g1 = _make_goal(status=GoalStatus.ACTIVE, priority=GoalPriority.HIGH)
        g2 = _make_goal(status=GoalStatus.ACTIVE, priority=GoalPriority.LOW)
        g3 = _make_goal(status=GoalStatus.DRAFT, priority=GoalPriority.HIGH)
        backend.goals.save(g1)
        backend.goals.save(g2)
        backend.goals.save(g3)
        result = backend.goals.list_all(status=GoalStatus.ACTIVE, priority=GoalPriority.HIGH)
        assert len(result) == 1
        assert result[0].goal_id == g1.goal_id

    def test_delete(self, backend: StorageBackend) -> None:
        goal = _make_goal()
        backend.goals.save(goal)
        backend.goals.delete(goal.goal_id)
        assert backend.goals.get(goal.goal_id) is None

    def test_delete_nonexistent(self, backend: StorageBackend) -> None:
        # Should not raise
        backend.goals.delete(uuid.uuid4())

    def test_save_overwrites(self, backend: StorageBackend) -> None:
        goal = _make_goal(title="Original")
        backend.goals.save(goal)
        goal.title = "Updated"
        backend.goals.save(goal)
        retrieved = backend.goals.get(goal.goal_id)
        assert retrieved is not None
        assert retrieved.title == "Updated"


# ---------------------------------------------------------------------------
# Task repository tests
# ---------------------------------------------------------------------------


class TestTaskRepository:
    def test_save_and_get(self, backend: StorageBackend) -> None:
        goal_id = uuid.uuid4()
        task = _make_task(goal_id)
        backend.tasks.save(task)
        retrieved = backend.tasks.get(task.task_id)
        assert retrieved is not None
        assert retrieved.task_id == task.task_id

    def test_get_nonexistent(self, backend: StorageBackend) -> None:
        assert backend.tasks.get(uuid.uuid4()) is None

    def test_list_by_goal(self, backend: StorageBackend) -> None:
        goal_id_a = uuid.uuid4()
        goal_id_b = uuid.uuid4()
        t1 = _make_task(goal_id_a, title="Task A1")
        t2 = _make_task(goal_id_a, title="Task A2")
        t3 = _make_task(goal_id_b, title="Task B1")
        backend.tasks.save(t1)
        backend.tasks.save(t2)
        backend.tasks.save(t3)
        tasks_a = backend.tasks.list_by_goal(goal_id_a)
        assert len(tasks_a) == 2
        tasks_b = backend.tasks.list_by_goal(goal_id_b)
        assert len(tasks_b) == 1

    def test_save_overwrites(self, backend: StorageBackend) -> None:
        goal_id = uuid.uuid4()
        task = _make_task(goal_id, title="Original")
        backend.tasks.save(task)
        task.title = "Updated"
        backend.tasks.save(task)
        retrieved = backend.tasks.get(task.task_id)
        assert retrieved is not None
        assert retrieved.title == "Updated"


# ---------------------------------------------------------------------------
# PipelineRun repository tests
# ---------------------------------------------------------------------------


class TestPipelineRunRepository:
    def test_save_and_get(self, backend: StorageBackend) -> None:
        run = _make_pipeline_run(agent_id="agent-1")
        backend.pipeline_runs.save(run)
        retrieved = backend.pipeline_runs.get(run.run_id)
        assert retrieved is not None
        assert retrieved.run_id == run.run_id

    def test_get_nonexistent(self, backend: StorageBackend) -> None:
        assert backend.pipeline_runs.get(uuid.uuid4()) is None

    def test_list_all(self, backend: StorageBackend) -> None:
        r1 = _make_pipeline_run(agent_id="agent-1")
        r2 = _make_pipeline_run(agent_id="agent-2")
        backend.pipeline_runs.save(r1)
        backend.pipeline_runs.save(r2)
        assert len(backend.pipeline_runs.list_all()) == 2

    def test_list_all_filter_agent_id(self, backend: StorageBackend) -> None:
        r1 = _make_pipeline_run(agent_id="agent-1")
        r2 = _make_pipeline_run(agent_id="agent-2")
        r3 = _make_pipeline_run(agent_id="agent-1")
        backend.pipeline_runs.save(r1)
        backend.pipeline_runs.save(r2)
        backend.pipeline_runs.save(r3)
        result = backend.pipeline_runs.list_all(agent_id="agent-1")
        assert len(result) == 2

    def test_save_overwrites(self, backend: StorageBackend) -> None:
        run = _make_pipeline_run(agent_id="agent-1")
        backend.pipeline_runs.save(run)
        run.mark_completed(PipelineStatus.PASSED)
        backend.pipeline_runs.save(run)
        retrieved = backend.pipeline_runs.get(run.run_id)
        assert retrieved is not None
        assert retrieved.status == PipelineStatus.PASSED


# ---------------------------------------------------------------------------
# AgentProfile repository tests
# ---------------------------------------------------------------------------


class TestAgentProfileRepository:
    def test_save_and_get(self, backend: StorageBackend) -> None:
        profile = _make_agent_profile("agent-x")
        backend.agent_profiles.save(profile)
        retrieved = backend.agent_profiles.get("agent-x")
        assert retrieved is not None
        assert retrieved.agent_id == "agent-x"

    def test_get_nonexistent(self, backend: StorageBackend) -> None:
        assert backend.agent_profiles.get("nonexistent") is None

    def test_list_all(self, backend: StorageBackend) -> None:
        backend.agent_profiles.save(_make_agent_profile("a1"))
        backend.agent_profiles.save(_make_agent_profile("a2"))
        assert len(backend.agent_profiles.list_all()) == 2

    def test_save_overwrites(self, backend: StorageBackend) -> None:
        profile = _make_agent_profile("agent-x", total_deployments=0)
        backend.agent_profiles.save(profile)
        profile.total_deployments = 10
        profile.successful_deployments = 9
        backend.agent_profiles.save(profile)
        retrieved = backend.agent_profiles.get("agent-x")
        assert retrieved is not None
        assert retrieved.total_deployments == 10


# ---------------------------------------------------------------------------
# Intent repository tests
# ---------------------------------------------------------------------------


class TestIntentRepository:
    def test_save_and_get(self, backend: StorageBackend) -> None:
        intent = _make_intent()
        backend.intents.save(intent)
        retrieved = backend.intents.get(intent.intent_id)
        assert retrieved is not None
        assert retrieved.intent_id == intent.intent_id

    def test_get_nonexistent(self, backend: StorageBackend) -> None:
        assert backend.intents.get(uuid.uuid4()) is None

    def test_list_all(self, backend: StorageBackend) -> None:
        backend.intents.save(_make_intent("a1"))
        backend.intents.save(_make_intent("a2"))
        assert len(backend.intents.list_all()) == 2

    def test_save_overwrites(self, backend: StorageBackend) -> None:
        intent = _make_intent(description="Original")
        backend.intents.save(intent)
        intent.description = "Updated"
        backend.intents.save(intent)
        retrieved = backend.intents.get(intent.intent_id)
        assert retrieved is not None
        assert retrieved.description == "Updated"


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestFactory:
    def test_create_memory(self) -> None:
        storage = create_storage("memory")
        assert storage is not None

    def test_create_sqlite(self, tmp_path) -> None:
        storage = create_storage("sqlite", db_path=str(tmp_path / "test.db"))
        assert storage is not None

    def test_invalid_backend(self) -> None:
        with pytest.raises(ValueError, match="Unknown storage backend"):
            create_storage("postgres")


# ---------------------------------------------------------------------------
# SQLite persistence tests
# ---------------------------------------------------------------------------


class TestSqlitePersistence:
    """Test that SQLite actually persists data across connections."""

    def test_goal_persists_across_connections(self, tmp_path) -> None:
        db_path = str(tmp_path / "persist.db")
        storage1 = create_storage("sqlite", db_path=db_path)
        goal = _make_goal(title="Persistent goal")
        storage1.goals.save(goal)

        # Create a new storage backend pointing to the same DB
        storage2 = create_storage("sqlite", db_path=db_path)
        retrieved = storage2.goals.get(goal.goal_id)
        assert retrieved is not None
        assert retrieved.title == "Persistent goal"

    def test_task_persists_across_connections(self, tmp_path) -> None:
        db_path = str(tmp_path / "persist.db")
        goal_id = uuid.uuid4()
        storage1 = create_storage("sqlite", db_path=db_path)
        task = _make_task(goal_id, title="Persistent task")
        storage1.tasks.save(task)

        storage2 = create_storage("sqlite", db_path=db_path)
        retrieved = storage2.tasks.get(task.task_id)
        assert retrieved is not None
        assert retrieved.title == "Persistent task"

    def test_pipeline_run_persists(self, tmp_path) -> None:
        db_path = str(tmp_path / "persist.db")
        storage1 = create_storage("sqlite", db_path=db_path)
        run = _make_pipeline_run(agent_id="agent-p")
        storage1.pipeline_runs.save(run)

        storage2 = create_storage("sqlite", db_path=db_path)
        retrieved = storage2.pipeline_runs.get(run.run_id)
        assert retrieved is not None
        assert retrieved.agent_id == "agent-p"

    def test_agent_profile_persists(self, tmp_path) -> None:
        db_path = str(tmp_path / "persist.db")
        storage1 = create_storage("sqlite", db_path=db_path)
        profile = _make_agent_profile("persistent-agent", total_deployments=5)
        storage1.agent_profiles.save(profile)

        storage2 = create_storage("sqlite", db_path=db_path)
        retrieved = storage2.agent_profiles.get("persistent-agent")
        assert retrieved is not None
        assert retrieved.total_deployments == 5

    def test_intent_persists(self, tmp_path) -> None:
        db_path = str(tmp_path / "persist.db")
        storage1 = create_storage("sqlite", db_path=db_path)
        intent = _make_intent(description="Persistent intent")
        storage1.intents.save(intent)

        storage2 = create_storage("sqlite", db_path=db_path)
        retrieved = storage2.intents.get(intent.intent_id)
        assert retrieved is not None
        assert retrieved.description == "Persistent intent"

    def test_tables_created_automatically(self, tmp_path) -> None:
        db_path = str(tmp_path / "auto_tables.db")
        storage = create_storage("sqlite", db_path=db_path)
        # Should work without errors — tables created on init
        assert storage.goals.list_all() == []
        assert storage.tasks.list_by_goal(uuid.uuid4()) == []
        assert storage.pipeline_runs.list_all() == []
        assert storage.agent_profiles.list_all() == []
        assert storage.intents.list_all() == []


# ---------------------------------------------------------------------------
# Protocol conformance tests
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_memory_implements_protocols(self) -> None:
        from src.storage.memory import (
            MemoryAgentProfileRepository,
            MemoryGoalRepository,
            MemoryIntentRepository,
            MemoryPipelineRunRepository,
            MemoryTaskRepository,
        )
        from src.storage.repositories import (
            AgentProfileRepository,
            GoalRepository,
            IntentRepository,
            PipelineRunRepository,
            TaskRepository,
        )

        assert isinstance(MemoryGoalRepository(), GoalRepository)
        assert isinstance(MemoryTaskRepository(), TaskRepository)
        assert isinstance(MemoryPipelineRunRepository(), PipelineRunRepository)
        assert isinstance(MemoryAgentProfileRepository(), AgentProfileRepository)
        assert isinstance(MemoryIntentRepository(), IntentRepository)

    def test_sqlite_implements_protocols(self, tmp_path) -> None:
        from src.storage.repositories import (
            AgentProfileRepository,
            GoalRepository,
            IntentRepository,
            PipelineRunRepository,
            TaskRepository,
        )
        from src.storage.sqlite import (
            SqliteAgentProfileRepository,
            SqliteGoalRepository,
            SqliteIntentRepository,
            SqlitePipelineRunRepository,
            SqliteTaskRepository,
        )

        db = str(tmp_path / "proto.db")
        assert isinstance(SqliteGoalRepository(db), GoalRepository)
        assert isinstance(SqliteTaskRepository(db), TaskRepository)
        assert isinstance(SqlitePipelineRunRepository(db), PipelineRunRepository)
        assert isinstance(SqliteAgentProfileRepository(db), AgentProfileRepository)
        assert isinstance(SqliteIntentRepository(db), IntentRepository)
