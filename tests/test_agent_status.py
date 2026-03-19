"""Tests for agent status tracking (Phase 2)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.leases.manager import AgentPhase, AgentStatus, LeaseManager


class TestAgentPhaseEnum:
    """Tests for the AgentPhase enum."""

    def test_all_phases_exist(self):
        phases = [
            "idle", "claiming", "calling_llm", "writing_files",
            "running_tests", "submitting", "waiting",
        ]
        for p in phases:
            assert AgentPhase(p) is not None

    def test_invalid_phase_raises(self):
        with pytest.raises(ValueError):
            AgentPhase("nonexistent")


class TestAgentStatusModel:
    """Tests for the AgentStatus model."""

    def test_defaults(self):
        s = AgentStatus(agent_id="test")
        assert s.phase == AgentPhase.IDLE
        assert s.current_task_id is None
        assert s.elapsed_seconds == 0.0

    def test_serialization(self):
        s = AgentStatus(
            agent_id="agent-1",
            phase=AgentPhase.CALLING_LLM,
            current_task_id=uuid.uuid4(),
            current_task_title="Fix bug",
            started_at=datetime.now(timezone.utc),
        )
        data = s.model_dump(mode="json")
        assert data["phase"] == "calling_llm"
        assert data["agent_id"] == "agent-1"
        assert data["current_task_title"] == "Fix bug"


class TestAgentStatusTracking:
    """Integration tests for status tracking via LeaseManager."""

    def test_claim_sets_claiming_phase(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        s = lm.get_agent_status("agent-1")
        assert s.phase == AgentPhase.CLAIMING
        assert s.current_task_id == task_id

    def test_heartbeat_updates_phase(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        lm.renew(task_id, "agent-1", phase=AgentPhase.WRITING_FILES)
        s = lm.get_agent_status("agent-1")
        assert s.phase == AgentPhase.WRITING_FILES

    def test_heartbeat_updates_last_heartbeat(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        before = lm.get_agent_status("agent-1").last_heartbeat
        lm.renew(task_id, "agent-1")
        after = lm.get_agent_status("agent-1").last_heartbeat
        assert after >= before

    def test_release_resets_to_idle(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")
        lm.renew(task_id, "agent-1", phase=AgentPhase.RUNNING_TESTS)

        lm.release(task_id, "agent-1")
        s = lm.get_agent_status("agent-1")
        assert s.phase == AgentPhase.IDLE
        assert s.current_task_id is None

    def test_sweep_resets_to_idle(self):
        lm = LeaseManager(lease_duration_seconds=0, grace_period_seconds=0)
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        lm.sweep_expired()
        s = lm.get_agent_status("agent-1")
        assert s.phase == AgentPhase.IDLE

    def test_multiple_agents_tracked(self):
        lm = LeaseManager()
        lm.claim(uuid.uuid4(), "agent-1")
        lm.claim(uuid.uuid4(), "agent-2")
        lm.claim(uuid.uuid4(), "agent-3")

        statuses = lm.get_all_agent_statuses()
        assert len(statuses) == 3

    def test_manual_status_update(self):
        lm = LeaseManager()
        task_id = uuid.uuid4()
        s = lm.update_agent_status(
            "agent-1",
            AgentPhase.CALLING_LLM,
            task_id=task_id,
            task_title="Fix auth",
        )
        assert s.phase == AgentPhase.CALLING_LLM
        assert s.current_task_title == "Fix auth"

    def test_phase_transition_timing(self):
        """Phase changes should reset started_at."""
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        lm.renew(task_id, "agent-1", phase=AgentPhase.CALLING_LLM)
        s1 = lm.get_agent_status("agent-1")
        t1 = s1.started_at

        lm.renew(task_id, "agent-1", phase=AgentPhase.WRITING_FILES)
        s2 = lm.get_agent_status("agent-1")
        assert s2.started_at >= t1

    def test_same_phase_heartbeat_no_reset(self):
        """Heartbeat with same phase should NOT reset started_at."""
        lm = LeaseManager()
        task_id = uuid.uuid4()
        lm.claim(task_id, "agent-1")

        lm.renew(task_id, "agent-1", phase=AgentPhase.CALLING_LLM)
        s1 = lm.get_agent_status("agent-1")
        t1 = s1.started_at

        # Same phase — started_at should not change
        lm.renew(task_id, "agent-1", phase=AgentPhase.CALLING_LLM)
        s2 = lm.get_agent_status("agent-1")
        assert s2.started_at == t1
