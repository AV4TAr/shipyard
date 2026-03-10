"""AgentRegistry — store for agent registrations with optional persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import AgentCapability, AgentRegistration, AgentStatus

if TYPE_CHECKING:
    from src.storage.repositories import AgentRegistrationRepository


# Built-in generic fallback agent that cannot be unregistered.
_GENERIC_AGENT = AgentRegistration(
    agent_id="generic",
    name="Generic Agent",
    capabilities=[AgentCapability.GENERIC, AgentCapability.FULLSTACK],
    primary_capability=AgentCapability.GENERIC,
    languages=[
        "python", "typescript", "javascript", "go", "rust", "java",
        "ruby", "c", "cpp", "csharp", "swift", "kotlin",
    ],
    frameworks=[],
    max_concurrent_tasks=10,
    status=AgentStatus.AVAILABLE,
)


class AgentRegistry:
    """Registry of available agents with optional persistence.

    Always contains a built-in GENERIC fallback agent that cannot be removed.
    When *registration_repo* is provided, registrations are persisted through
    that repository.
    """

    def __init__(
        self,
        *,
        registration_repo: AgentRegistrationRepository | None = None,
    ) -> None:
        self._repo = registration_repo
        self._agents: dict[str, AgentRegistration] = {
            _GENERIC_AGENT.agent_id: _GENERIC_AGENT.model_copy(),
        }
        # Load persisted registrations on startup.
        if self._repo is not None:
            for reg in self._repo.list_all():
                self._agents[reg.agent_id] = reg

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, registration: AgentRegistration) -> None:
        """Register or update an agent."""
        self._agents[registration.agent_id] = registration
        if self._repo is not None:
            self._repo.save(registration)

    def unregister(self, agent_id: str) -> None:
        """Remove an agent from the registry.

        The built-in generic agent cannot be unregistered.

        Raises:
            ValueError: If attempting to unregister the generic agent.
            KeyError: If the agent does not exist.
        """
        if agent_id == "generic":
            raise ValueError("Cannot unregister the built-in generic agent")
        if agent_id not in self._agents:
            raise KeyError(f"Agent {agent_id!r} not found")
        del self._agents[agent_id]
        if self._repo is not None:
            self._repo.delete(agent_id)

    def update_status(self, agent_id: str, status: AgentStatus) -> None:
        """Update an agent's operational status.

        Raises:
            KeyError: If the agent does not exist.
        """
        if agent_id not in self._agents:
            raise KeyError(f"Agent {agent_id!r} not found")
        agent = self._agents[agent_id]
        self._agents[agent_id] = agent.model_copy(update={"status": status})

    def get(self, agent_id: str) -> AgentRegistration:
        """Retrieve an agent by ID.

        Raises:
            KeyError: If the agent does not exist.
        """
        if agent_id not in self._agents:
            raise KeyError(f"Agent {agent_id!r} not found")
        return self._agents[agent_id]

    def list_agents(
        self,
        capability: AgentCapability | None = None,
        status: AgentStatus | None = None,
    ) -> list[AgentRegistration]:
        """List all agents, optionally filtered by capability and/or status."""
        agents = list(self._agents.values())
        if capability is not None:
            agents = [a for a in agents if capability in a.capabilities]
        if status is not None:
            agents = [a for a in agents if a.status == status]
        return agents

    def get_available(
        self, capability: AgentCapability | None = None
    ) -> list[AgentRegistration]:
        """Return only AVAILABLE agents, optionally filtered by capability."""
        return self.list_agents(capability=capability, status=AgentStatus.AVAILABLE)
