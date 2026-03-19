"""Abstract repository protocols for each data domain."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from src.goals.models import AgentTask, Goal, GoalPriority, GoalStatus
from src.intent.schema import IntentDeclaration
from src.pipeline.models import PipelineRun
from src.routing.models import AgentRegistration
from src.trust.models import AgentProfile


@runtime_checkable
class GoalRepository(Protocol):
    """Repository for Goal entities."""

    def save(self, goal: Goal) -> None: ...
    def get(self, goal_id: uuid.UUID) -> Goal | None: ...
    def list_all(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
    ) -> list[Goal]: ...
    def delete(self, goal_id: uuid.UUID) -> None: ...


@runtime_checkable
class TaskRepository(Protocol):
    """Repository for AgentTask entities."""

    def save(self, task: AgentTask) -> None: ...
    def get(self, task_id: uuid.UUID) -> AgentTask | None: ...
    def list_by_goal(self, goal_id: uuid.UUID) -> list[AgentTask]: ...


@runtime_checkable
class PipelineRunRepository(Protocol):
    """Repository for PipelineRun entities."""

    def save(self, run: PipelineRun) -> None: ...
    def get(self, run_id: uuid.UUID) -> PipelineRun | None: ...
    def list_all(self, agent_id: str | None = None) -> list[PipelineRun]: ...


@runtime_checkable
class AgentProfileRepository(Protocol):
    """Repository for AgentProfile entities."""

    def save(self, profile: AgentProfile) -> None: ...
    def get(self, agent_id: str) -> AgentProfile | None: ...
    def list_all(self) -> list[AgentProfile]: ...


@runtime_checkable
class IntentRepository(Protocol):
    """Repository for IntentDeclaration entities."""

    def save(self, intent: IntentDeclaration) -> None: ...
    def get(self, intent_id: uuid.UUID) -> IntentDeclaration | None: ...
    def list_all(self) -> list[IntentDeclaration]: ...


@runtime_checkable
class AgentRegistrationRepository(Protocol):
    """Repository for routing AgentRegistration entities."""

    def save(self, registration: AgentRegistration) -> None: ...
    def get(self, agent_id: str) -> AgentRegistration | None: ...
    def list_all(self) -> list[AgentRegistration]: ...
    def delete(self, agent_id: str) -> None: ...


@runtime_checkable
class ProjectRepository(Protocol):
    """Repository for Project entities."""

    def save(self, project: "Project") -> None: ...
    def get(self, project_id: uuid.UUID) -> "Project | None": ...
    def list_all(self, status: str | None = None) -> "list[Project]": ...
    def delete(self, project_id: uuid.UUID) -> None: ...
