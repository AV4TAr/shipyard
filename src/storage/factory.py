"""Factory for creating storage backends."""

from __future__ import annotations

from dataclasses import dataclass

from src.storage.repositories import (
    AgentProfileRepository,
    AgentRegistrationRepository,
    GoalRepository,
    IntentRepository,
    PipelineRunRepository,
    TaskRepository,
)


@dataclass
class StorageBackend:
    """Container holding all repository instances."""

    goals: GoalRepository
    tasks: TaskRepository
    pipeline_runs: PipelineRunRepository
    agent_profiles: AgentProfileRepository
    intents: IntentRepository
    agent_registrations: AgentRegistrationRepository | None = None


def create_storage(
    backend: str = "memory", db_path: str | None = None
) -> StorageBackend:
    """Create a StorageBackend with all repositories for the given backend type.

    Args:
        backend: Either ``"memory"`` or ``"sqlite"``.
        db_path: Path for the SQLite database file.  Only used when
            ``backend="sqlite"``.  Defaults to ``"data/ai-cicd.db"``.

    Returns:
        A :class:`StorageBackend` with all repositories initialised.

    Raises:
        ValueError: If *backend* is not a recognised value.
    """
    if backend == "memory":
        from src.storage.memory import (
            MemoryAgentProfileRepository,
            MemoryAgentRegistrationRepository,
            MemoryGoalRepository,
            MemoryIntentRepository,
            MemoryPipelineRunRepository,
            MemoryTaskRepository,
        )

        return StorageBackend(
            goals=MemoryGoalRepository(),
            tasks=MemoryTaskRepository(),
            pipeline_runs=MemoryPipelineRunRepository(),
            agent_profiles=MemoryAgentProfileRepository(),
            intents=MemoryIntentRepository(),
            agent_registrations=MemoryAgentRegistrationRepository(),
        )

    if backend == "sqlite":
        from src.storage.sqlite import (
            SqliteAgentProfileRepository,
            SqliteAgentRegistrationRepository,
            SqliteGoalRepository,
            SqliteIntentRepository,
            SqlitePipelineRunRepository,
            SqliteTaskRepository,
        )

        path = db_path or "data/ai-cicd.db"
        return StorageBackend(
            goals=SqliteGoalRepository(path),
            tasks=SqliteTaskRepository(path),
            pipeline_runs=SqlitePipelineRunRepository(path),
            agent_profiles=SqliteAgentProfileRepository(path),
            intents=SqliteIntentRepository(path),
            agent_registrations=SqliteAgentRegistrationRepository(path),
        )

    raise ValueError(f"Unknown storage backend: {backend!r}. Use 'memory' or 'sqlite'.")
