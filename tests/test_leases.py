"""Tests for the lease-based task claim system."""

import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.goals.models import AgentTask, TaskStatus
from src.intent.schema import RiskLevel
from src.leases.manager import AgentPhase, AgentStatus, LeaseInfo, LeaseManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(goal_id: uuid.UUID | None = None, **kwargs) -> AgentTask:
    """Create a minimal AgentTask for testing."""
    return AgentTask(
        goal_id=goal_id or uuid.uuid4(),
        title=kwargs.get("title", "Test task"),
        description=kwargs.get("description", "A test task"),
        **{k: v for k, v in kwargs.items() if k not in ("title", "description")},
    )


# ---------------------------------------------------------------------------
# LeaseManager — claim
# ---------------------------------------------------------------------------


class TestLeaseClaim:
    """Tests for LeaseManager.claim()."""

    def test_claim_returns_lease_info(self):
        lm = LeaseManager(lease_duration_seconds=60)
        task_id = uuid.uuid4()
        lease = lm.claim(task_id, "agent-1")

        assert isinstance(lease, LeaseInfo)
        assert lease.task_id == task_id
        assert lease.agent_id == "agent-1"
        assert lease.lease_duration_seconds == 60
        assert lease.lease_expires_at > datetime.now(timezone.utc)

    def test_claim_sets_agent_status(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        status = lm.get_agent_status("agent-1")
        assert status is not None
        assert status.phase == AgentPhase.CLAIMING
        assert status.current_task_id == task_id

    def test_double_claim_raises(self):
        lm = LeaseManager(lease_duration_seconds=60)
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        with pytest.raises(ValueError, match="already has an active lease"):
            lm.claim(task_id, "agent-2")

    def test_claim_after_expiry_succeeds(self):
        lm = LeaseManager(lease_duration_seconds=0)  # instant expiry
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        # Lease already expired, agent-2 should be able to claim
        lease = lm.claim(task_id, "agent-2")
        assert lease.agent_id == "agent-2"

    def test_get_lease_returns_correct_info(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        lease = lm.get_lease(task_id)
        assert lease is not None
        assert lease.agent_id == "agent-1"

    def test_get_lease_returns_none_for_unknown(self):
        lm = LeaseManager()
        assert lm.get_lease(uuid.uuid4()) is None

    def test_get_active_leases(self):
        lm = LeaseManager(lease_duration_seconds=60)
        t1, t2 = uuid.uuid4(), uuid.uuid4()
        lm.claim(t1, "agent-1")
        lm.claim(t2, "agent-2")

        active = lm.get_active_leases()
        assert len(active) == 2
        task_ids = {l.task_id for l in active}
        assert t1 in task_ids
        assert t2 in task_ids


# ---------------------------------------------------------------------------
# LeaseManager — renew (heartbeat)
# ---------------------------------------------------------------------------


class TestLeaseRenew:
    """Tests for LeaseManager.renew()."""

    def test_renew_extends_expiry(self):
        lm = LeaseManager(lease_duration_seconds=60)
        task_id = uuid.uuid4()
        original = lm.claim(task_id, "agent-1")

        renewed = lm.renew(task_id, "agent-1")
        assert renewed.lease_expires_at >= original.lease_expires_at

    def test_renew_with_phase_update(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        lm.renew(task_id, "agent-1", phase=AgentPhase.WRITING_FILES)
        status = lm.get_agent_status("agent-1")
        assert status.phase == AgentPhase.WRITING_FILES

    def test_renew_unknown_task_raises(self):
        lm = LeaseManager()
        with pytest.raises(KeyError, match="No active lease"):
            lm.renew(uuid.uuid4(), "agent-1")

    def test_renew_wrong_agent_raises(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        with pytest.raises(ValueError, match="does not own the lease"):
            lm.renew(task_id, "agent-2")


# ---------------------------------------------------------------------------
# LeaseManager — release
# ---------------------------------------------------------------------------


class TestLeaseRelease:
    """Tests for LeaseManager.release()."""

    def test_release_removes_lease(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        lm.release(task_id, "agent-1")
        assert lm.get_lease(task_id) is None

    def test_release_sets_agent_idle(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        lm.release(task_id, "agent-1")
        status = lm.get_agent_status("agent-1")
        assert status.phase == AgentPhase.IDLE
        assert status.current_task_id is None

    def test_release_unknown_task_raises(self):
        lm = LeaseManager()
        with pytest.raises(KeyError):
            lm.release(uuid.uuid4(), "agent-1")

    def test_release_wrong_agent_raises(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        with pytest.raises(ValueError, match="does not own the lease"):
            lm.release(task_id, "agent-2")


# ---------------------------------------------------------------------------
# LeaseManager — sweep
# ---------------------------------------------------------------------------


class TestLeaseSweep:
    """Tests for LeaseManager.sweep_expired()."""

    def test_sweep_resets_expired_tasks(self):
        mock_gm = MagicMock()
        lm = LeaseManager(
            lease_duration_seconds=0,
            grace_period_seconds=0,
            goal_manager=mock_gm,
        )
        t1 = uuid.uuid4()
        lm.claim(t1, "agent-1")

        # Lease is already expired (duration=0, grace=0)
        expired = lm.sweep_expired()
        assert t1 in expired
        mock_gm.update_task_status.assert_called_once_with(
            t1, TaskStatus.PENDING
        )

    def test_sweep_skips_active_leases(self):
        lm = LeaseManager(lease_duration_seconds=300)
        lm.claim(uuid.uuid4(), "agent-1")

        expired = lm.sweep_expired()
        assert expired == []

    def test_sweep_clears_agent_status(self):
        lm = LeaseManager(
            lease_duration_seconds=0,
            grace_period_seconds=0,
        )
        t1 = uuid.uuid4()
        lm.claim(t1, "agent-1")

        lm.sweep_expired()
        status = lm.get_agent_status("agent-1")
        assert status.phase == AgentPhase.IDLE
        assert status.current_task_id is None

    def test_sweep_multiple_expired(self):
        mock_gm = MagicMock()
        lm = LeaseManager(
            lease_duration_seconds=0,
            grace_period_seconds=0,
            goal_manager=mock_gm,
        )
        t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        lm.claim(t1, "agent-1")
        lm.claim(t2, "agent-2")

        # t3 with longer lease
        lm2 = LeaseManager(lease_duration_seconds=300, goal_manager=mock_gm)
        lm2._leases = lm._leases.copy()
        lm2._agent_statuses = lm._agent_statuses.copy()
        lm2.grace_period_seconds = 0
        lm2.claim(t3, "agent-3")

        expired = lm2.sweep_expired()
        assert t1 in expired
        assert t2 in expired
        assert t3 not in expired


# ---------------------------------------------------------------------------
# Agent status tracking
# ---------------------------------------------------------------------------


class TestAgentStatus:
    """Tests for agent status tracking in LeaseManager."""

    def test_get_all_statuses(self):
        lm = LeaseManager()
        lm.claim(uuid.uuid4(), "agent-1")
        lm.claim(uuid.uuid4(), "agent-2")

        statuses = lm.get_all_agent_statuses()
        assert len(statuses) == 2
        agent_ids = {s.agent_id for s in statuses}
        assert agent_ids == {"agent-1", "agent-2"}

    def test_update_agent_status(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()

        status = lm.update_agent_status(
            "agent-1", AgentPhase.CALLING_LLM, task_id=task_id
        )
        assert status.phase == AgentPhase.CALLING_LLM
        assert status.current_task_id == task_id

    def test_phase_change_resets_started_at(self):
        lm = LeaseManager()
        s1 = lm.update_agent_status("agent-1", AgentPhase.CALLING_LLM)
        first_started = s1.started_at

        s2 = lm.update_agent_status("agent-1", AgentPhase.WRITING_FILES)
        assert s2.started_at >= first_started
        assert s2.phase == AgentPhase.WRITING_FILES

    def test_elapsed_seconds(self):
        status = AgentStatus(
            agent_id="test",
            phase=AgentPhase.CALLING_LLM,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
        assert status.elapsed_seconds >= 5.0

    def test_elapsed_seconds_no_start(self):
        status = AgentStatus(agent_id="test", phase=AgentPhase.IDLE)
        assert status.elapsed_seconds == 0.0

    def test_status_dispatches_event(self):
        mock_dispatcher = MagicMock()
        lm = LeaseManager(event_dispatcher=mock_dispatcher)
        lm.claim(uuid.uuid4(), "agent-1")

        # Heartbeat triggers status update
        lm.renew(
            list(lm._leases.keys())[0],
            "agent-1",
            phase=AgentPhase.RUNNING_TESTS,
        )
        # Should have dispatched at least one event
        assert mock_dispatcher.dispatch.called


# ---------------------------------------------------------------------------
# LeaseInfo model
# ---------------------------------------------------------------------------


class TestLeaseInfo:
    """Tests for the LeaseInfo model."""

    def test_serialization(self):
        info = LeaseInfo(
            task_id=uuid.uuid4(),
            agent_id="agent-1",
            lease_expires_at=datetime.now(timezone.utc),
            lease_duration_seconds=60,
            heartbeat_interval_seconds=30,
        )
        data = info.model_dump(mode="json")
        assert data["agent_id"] == "agent-1"
        assert data["lease_duration_seconds"] == 60


# ---------------------------------------------------------------------------
# Integration: LeaseManager with model fields
# ---------------------------------------------------------------------------


class TestLeaseModelFields:
    """Tests that new Optional fields on AgentTask don't break anything."""

    def test_agent_task_default_lease_fields(self):
        task = _make_task()
        assert task.claimed_by is None
        assert task.claimed_at is None
        assert task.lease_expires_at is None
        assert task.worktree_path is None
        assert task.branch_name is None

    def test_agent_task_with_lease_fields(self):
        now = datetime.now(timezone.utc)
        task = _make_task(
            claimed_by="agent-1",
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=60),
        )
        assert task.claimed_by == "agent-1"
        assert task.lease_expires_at > now

    def test_agent_task_serialization_roundtrip(self):
        now = datetime.now(timezone.utc)
        task = _make_task(
            claimed_by="agent-1",
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=60),
            worktree_path="/tmp/worktree",
            branch_name="task/abc123-test",
        )
        data = task.model_dump(mode="json")
        restored = AgentTask.model_validate(data)
        assert restored.claimed_by == "agent-1"
        assert restored.worktree_path == "/tmp/worktree"
        assert restored.branch_name == "task/abc123-test"

    def test_agent_task_without_new_fields_still_works(self):
        """Simulate deserializing old data that doesn't have the new fields."""
        data = {
            "task_id": str(uuid.uuid4()),
            "goal_id": str(uuid.uuid4()),
            "title": "Old task",
            "description": "From before lease fields existed",
            "status": "pending",
        }
        task = AgentTask.model_validate(data)
        assert task.claimed_by is None
        assert task.lease_expires_at is None
        assert task.worktree_path is None
