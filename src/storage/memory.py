"""In-memory implementations of all repositories."""

from __future__ import annotations

import uuid

from src.goals.models import AgentTask, Goal, GoalPriority, GoalStatus
from src.intent.schema import IntentDeclaration
from src.pipeline.models import PipelineRun
from src.trust.models import AgentProfile


class MemoryGoalRepository:
    """In-memory Goal repository backed by a dict."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, Goal] = {}

    def save(self, goal: Goal) -> None:
        self._store[goal.goal_id] = goal

    def get(self, goal_id: uuid.UUID) -> Goal | None:
        return self._store.get(goal_id)

    def list_all(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
    ) -> list[Goal]:
        results = list(self._store.values())
        if status is not None:
            results = [g for g in results if g.status == status]
        if priority is not None:
            results = [g for g in results if g.priority == priority]
        return results

    def delete(self, goal_id: uuid.UUID) -> None:
        self._store.pop(goal_id, None)


class MemoryTaskRepository:
    """In-memory AgentTask repository backed by a dict."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, AgentTask] = {}

    def save(self, task: AgentTask) -> None:
        self._store[task.task_id] = task

    def get(self, task_id: uuid.UUID) -> AgentTask | None:
        return self._store.get(task_id)

    def list_by_goal(self, goal_id: uuid.UUID) -> list[AgentTask]:
        return [t for t in self._store.values() if t.goal_id == goal_id]


class MemoryPipelineRunRepository:
    """In-memory PipelineRun repository backed by a dict."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, PipelineRun] = {}

    def save(self, run: PipelineRun) -> None:
        self._store[run.run_id] = run

    def get(self, run_id: uuid.UUID) -> PipelineRun | None:
        return self._store.get(run_id)

    def list_all(self, agent_id: str | None = None) -> list[PipelineRun]:
        results = list(self._store.values())
        if agent_id is not None:
            results = [r for r in results if r.agent_id == agent_id]
        return results


class MemoryAgentProfileRepository:
    """In-memory AgentProfile repository backed by a dict."""

    def __init__(self) -> None:
        self._store: dict[str, AgentProfile] = {}

    def save(self, profile: AgentProfile) -> None:
        self._store[profile.agent_id] = profile

    def get(self, agent_id: str) -> AgentProfile | None:
        return self._store.get(agent_id)

    def list_all(self) -> list[AgentProfile]:
        return list(self._store.values())


class MemoryIntentRepository:
    """In-memory IntentDeclaration repository backed by a dict."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, IntentDeclaration] = {}

    def save(self, intent: IntentDeclaration) -> None:
        self._store[intent.intent_id] = intent

    def get(self, intent_id: uuid.UUID) -> IntentDeclaration | None:
        return self._store.get(intent_id)

    def list_all(self) -> list[IntentDeclaration]:
        return list(self._store.values())
