"""Lease manager for task claims with heartbeat-based renewal."""

from __future__ import annotations

import enum
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent status tracking (Phase 2)
# ---------------------------------------------------------------------------


class AgentPhase(str, enum.Enum):
    """Current activity phase of an agent."""

    IDLE = "idle"
    CLAIMING = "claiming"
    CALLING_LLM = "calling_llm"
    WRITING_FILES = "writing_files"
    RUNNING_TESTS = "running_tests"
    SUBMITTING = "submitting"
    WAITING = "waiting"


class AgentStatus(BaseModel):
    """Snapshot of an agent's current status."""

    agent_id: str
    phase: AgentPhase = AgentPhase.IDLE
    current_task_id: Optional[uuid.UUID] = None
    current_task_title: Optional[str] = None
    last_heartbeat: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: Optional[datetime] = None

    @property
    def elapsed_seconds(self) -> float:
        """Seconds since the agent started its current activity."""
        if self.started_at is None:
            return 0.0
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()


# ---------------------------------------------------------------------------
# Lease info
# ---------------------------------------------------------------------------


class LeaseInfo(BaseModel):
    """Lease details returned to the agent after claiming a task."""

    task_id: uuid.UUID
    agent_id: str
    lease_expires_at: datetime
    lease_duration_seconds: int
    heartbeat_interval_seconds: int


# ---------------------------------------------------------------------------
# Lease manager
# ---------------------------------------------------------------------------


class PipelineFrozenError(ValueError):
    """Raised when an operation is attempted while the pipeline is frozen."""
    pass


class AgentBannedError(ValueError):
    """Raised when a banned agent attempts an operation."""
    pass


class LeaseManager:
    """Manages task leases with heartbeat renewal and automatic expiry sweep.

    Parameters:
        lease_duration_seconds: How long a lease lasts before expiring.
        heartbeat_interval_seconds: Recommended heartbeat interval for agents.
        grace_period_seconds: Extra grace after expiry before sweep resets.
        sweep_interval_seconds: How often the background sweep runs.
        goal_manager: GoalManager instance for task status updates.
        event_dispatcher: Optional EventDispatcher for broadcasting events.
    """

    def __init__(
        self,
        *,
        lease_duration_seconds: int = 120,
        heartbeat_interval_seconds: int = 30,
        grace_period_seconds: int = 15,
        sweep_interval_seconds: int = 30,
        goal_manager: Any = None,
        event_dispatcher: Any = None,
    ) -> None:
        self.lease_duration_seconds = lease_duration_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.grace_period_seconds = grace_period_seconds
        self.sweep_interval_seconds = sweep_interval_seconds
        self._goal_manager = goal_manager
        self._event_dispatcher = event_dispatcher

        # task_id → LeaseInfo
        self._leases: dict[uuid.UUID, LeaseInfo] = {}
        # agent_id → AgentStatus
        self._agent_statuses: dict[str, AgentStatus] = {}

        # Global pipeline freeze
        self._frozen: bool = False

        # Banned agents: agent_id → reason
        self._banned_agents: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Pipeline freeze (kill switch)
    # ------------------------------------------------------------------

    @property
    def frozen(self) -> bool:
        """Whether the pipeline is currently frozen."""
        return self._frozen

    def freeze(self) -> None:
        """Freeze the pipeline — block all new claims and submissions."""
        self._frozen = True
        logger.warning("Pipeline FROZEN — all new claims and submissions blocked")

    def unfreeze(self) -> None:
        """Unfreeze the pipeline — allow claims and submissions again."""
        self._frozen = False
        logger.info("Pipeline UNFROZEN — claims and submissions allowed")

    # ------------------------------------------------------------------
    # Agent banning
    # ------------------------------------------------------------------

    def ban_agent(self, agent_id: str, reason: str = "") -> None:
        """Ban an agent from claiming tasks."""
        self._banned_agents[agent_id] = reason
        logger.warning("Agent %s BANNED: %s", agent_id, reason or "no reason given")

    def unban_agent(self, agent_id: str) -> None:
        """Remove a ban on an agent."""
        self._banned_agents.pop(agent_id, None)
        logger.info("Agent %s unbanned", agent_id)

    def is_agent_banned(self, agent_id: str) -> bool:
        """Check if an agent is banned."""
        return agent_id in self._banned_agents

    def get_banned_agents(self) -> dict[str, str]:
        """Return all banned agents as {agent_id: reason}."""
        return dict(self._banned_agents)

    # ------------------------------------------------------------------
    # Lease operations
    # ------------------------------------------------------------------

    def claim(self, task_id: uuid.UUID, agent_id: str) -> LeaseInfo:
        """Claim a task with a lease.

        Raises:
            PipelineFrozenError: If the pipeline is frozen.
            AgentBannedError: If the agent is banned.
            ValueError: If the task already has an active lease.
        """
        if self._frozen:
            raise PipelineFrozenError(
                "Pipeline is frozen — no new claims allowed"
            )
        if agent_id in self._banned_agents:
            raise AgentBannedError(
                f"Agent {agent_id} is banned: "
                f"{self._banned_agents[agent_id] or 'no reason given'}"
            )
        # Check for existing active lease
        existing = self._leases.get(task_id)
        if existing is not None:
            if existing.lease_expires_at > datetime.now(timezone.utc):
                raise ValueError(
                    f"Task {task_id} already has an active lease "
                    f"held by {existing.agent_id}"
                )
            # Expired lease — allow re-claim
            del self._leases[task_id]

        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.lease_duration_seconds)

        lease = LeaseInfo(
            task_id=task_id,
            agent_id=agent_id,
            lease_expires_at=expires,
            lease_duration_seconds=self.lease_duration_seconds,
            heartbeat_interval_seconds=self.heartbeat_interval_seconds,
        )
        self._leases[task_id] = lease

        # Update agent status
        self._agent_statuses[agent_id] = AgentStatus(
            agent_id=agent_id,
            phase=AgentPhase.CLAIMING,
            current_task_id=task_id,
            last_heartbeat=now,
            started_at=now,
        )

        logger.info(
            "Agent %s claimed task %s (lease expires %s)",
            agent_id,
            task_id,
            expires.isoformat(),
        )
        return lease

    def renew(
        self,
        task_id: uuid.UUID,
        agent_id: str,
        phase: AgentPhase | None = None,
    ) -> LeaseInfo:
        """Renew (heartbeat) an existing lease.

        Raises:
            KeyError: If no lease exists for this task.
            ValueError: If the agent doesn't own the lease.
        """
        lease = self._leases.get(task_id)
        if lease is None:
            raise KeyError(f"No active lease for task {task_id}")

        if lease.agent_id != agent_id:
            raise ValueError(
                f"Agent {agent_id} does not own the lease for task {task_id} "
                f"(owned by {lease.agent_id})"
            )

        now = datetime.now(timezone.utc)
        new_expires = now + timedelta(seconds=self.lease_duration_seconds)
        lease.lease_expires_at = new_expires
        self._leases[task_id] = lease

        # Update agent status
        status = self._agent_statuses.get(agent_id)
        if status is not None:
            status.last_heartbeat = now
            if phase is not None:
                if status.phase != phase:
                    status.started_at = now
                status.phase = phase
        else:
            self._agent_statuses[agent_id] = AgentStatus(
                agent_id=agent_id,
                phase=phase or AgentPhase.IDLE,
                current_task_id=task_id,
                last_heartbeat=now,
                started_at=now,
            )

        # Broadcast status update
        self._dispatch_status_update(agent_id)

        return lease

    def release(self, task_id: uuid.UUID, agent_id: str) -> None:
        """Explicitly release a lease (task completed or abandoned).

        Raises:
            KeyError: If no lease exists for this task.
            ValueError: If the agent doesn't own the lease.
        """
        lease = self._leases.get(task_id)
        if lease is None:
            raise KeyError(f"No active lease for task {task_id}")

        if lease.agent_id != agent_id:
            raise ValueError(
                f"Agent {agent_id} does not own the lease for task {task_id}"
            )

        del self._leases[task_id]

        # Update agent status to idle
        status = self._agent_statuses.get(agent_id)
        if status is not None:
            status.phase = AgentPhase.IDLE
            status.current_task_id = None
            status.current_task_title = None
            status.started_at = None
            self._dispatch_status_update(agent_id)

        logger.info("Agent %s released lease for task %s", agent_id, task_id)

    def get_lease(self, task_id: uuid.UUID) -> LeaseInfo | None:
        """Return the current lease for a task, or None."""
        return self._leases.get(task_id)

    def get_active_leases(self) -> list[LeaseInfo]:
        """Return all active (non-expired) leases."""
        now = datetime.now(timezone.utc)
        return [
            lease
            for lease in self._leases.values()
            if lease.lease_expires_at > now
        ]

    def revoke(self, task_id: uuid.UUID) -> bool:
        """Force-expire a lease regardless of owner. Resets task to PENDING.

        Returns True if a lease was revoked, False if none existed.
        """
        from src.goals.models import TaskStatus

        lease = self._leases.pop(task_id, None)
        if lease is None:
            return False

        agent_id = lease.agent_id

        # Reset the task to PENDING if we have a goal manager
        if self._goal_manager is not None:
            try:
                self._goal_manager.update_task_status(
                    task_id, TaskStatus.PENDING
                )
            except Exception:
                logger.exception(
                    "Failed to reset task %s after lease revocation", task_id
                )

        # Update agent status
        status = self._agent_statuses.get(agent_id)
        if status is not None and status.current_task_id == task_id:
            status.phase = AgentPhase.IDLE
            status.current_task_id = None
            status.current_task_title = None
            status.started_at = None
            self._dispatch_status_update(agent_id)

        logger.warning(
            "Lease for task %s (agent %s) REVOKED",
            task_id,
            agent_id,
        )
        return True

    # ------------------------------------------------------------------
    # Background sweep
    # ------------------------------------------------------------------

    def sweep_expired(self) -> list[uuid.UUID]:
        """Find and reset expired leases.

        Returns the list of task IDs that were reset to PENDING.
        """
        from src.goals.models import TaskStatus

        now = datetime.now(timezone.utc)
        grace = timedelta(seconds=self.grace_period_seconds)
        expired_tasks: list[uuid.UUID] = []

        for task_id, lease in list(self._leases.items()):
            if lease.lease_expires_at + grace < now:
                expired_tasks.append(task_id)
                agent_id = lease.agent_id
                del self._leases[task_id]

                # Reset the task to PENDING if we have a goal manager
                if self._goal_manager is not None:
                    try:
                        self._goal_manager.update_task_status(
                            task_id, TaskStatus.PENDING
                        )
                        logger.warning(
                            "Lease expired for task %s (agent %s) — "
                            "reset to PENDING",
                            task_id,
                            agent_id,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to reset task %s after lease expiry",
                            task_id,
                        )

                # Update agent status
                status = self._agent_statuses.get(agent_id)
                if status is not None and status.current_task_id == task_id:
                    status.phase = AgentPhase.IDLE
                    status.current_task_id = None
                    status.current_task_title = None
                    status.started_at = None
                    self._dispatch_status_update(agent_id)

        if expired_tasks:
            logger.info("Swept %d expired leases", len(expired_tasks))

        return expired_tasks

    # ------------------------------------------------------------------
    # Agent status (Phase 2)
    # ------------------------------------------------------------------

    def get_agent_status(self, agent_id: str) -> AgentStatus | None:
        """Return the current status of an agent."""
        return self._agent_statuses.get(agent_id)

    def get_all_agent_statuses(self) -> list[AgentStatus]:
        """Return statuses for all known agents."""
        return list(self._agent_statuses.values())

    def update_agent_status(
        self,
        agent_id: str,
        phase: AgentPhase,
        task_id: uuid.UUID | None = None,
        task_title: str | None = None,
    ) -> AgentStatus:
        """Manually update an agent's status."""
        now = datetime.now(timezone.utc)
        existing = self._agent_statuses.get(agent_id)

        if existing is not None:
            if existing.phase != phase:
                existing.started_at = now
            existing.phase = phase
            existing.last_heartbeat = now
            if task_id is not None:
                existing.current_task_id = task_id
            if task_title is not None:
                existing.current_task_title = task_title
            status = existing
        else:
            status = AgentStatus(
                agent_id=agent_id,
                phase=phase,
                current_task_id=task_id,
                current_task_title=task_title,
                last_heartbeat=now,
                started_at=now,
            )
            self._agent_statuses[agent_id] = status

        self._dispatch_status_update(agent_id)
        return status

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def _dispatch_status_update(self, agent_id: str) -> None:
        """Broadcast an agent status update event."""
        if self._event_dispatcher is None:
            return
        status = self._agent_statuses.get(agent_id)
        if status is None:
            return
        try:
            from src.notifications.models import Event, EventType

            event = Event(
                event_type=EventType.AGENT_STATUS_UPDATED,
                timestamp=datetime.now(timezone.utc),
                data={
                    "agent_id": agent_id,
                    "phase": status.phase.value,
                    "current_task_id": str(status.current_task_id)
                    if status.current_task_id
                    else None,
                    "current_task_title": status.current_task_title,
                    "elapsed_seconds": status.elapsed_seconds,
                    "description": f"Agent {agent_id} is {status.phase.value}",
                },
            )
            self._event_dispatcher.dispatch(event)
        except Exception:
            logger.debug(
                "Failed to dispatch agent status update for %s",
                agent_id,
                exc_info=True,
            )
