"""Tests for the Agent Coordination Layer."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.coordination.claims import ClaimManager
from src.coordination.merge import Change, SemanticMergeChecker
from src.coordination.models import Claim, ConflictResolution
from src.coordination.queue import DeployQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_claim(
    *,
    agent_id: str = "agent-1",
    paths: list[str] | None = None,
    priority: int = 0,
    ttl_seconds: int = 3600,
    intent_id: uuid.UUID | None = None,
) -> Claim:
    now = datetime.now(timezone.utc)
    return Claim(
        agent_id=agent_id,
        intent_id=intent_id or uuid.uuid4(),
        paths=paths or ["src/**/*.py"],
        expires_at=now + timedelta(seconds=ttl_seconds),
        priority=priority,
    )


def _make_expired_claim(**kwargs) -> Claim:
    return _make_claim(ttl_seconds=-1, **kwargs)


# ---------------------------------------------------------------------------
# ClaimManager — acquisition & release
# ---------------------------------------------------------------------------

class TestClaimAcquireRelease:
    def test_acquire_no_conflict(self) -> None:
        mgr = ClaimManager()
        claim = _make_claim()
        conflict = mgr.acquire(claim)
        assert conflict is None
        assert len(mgr.get_active_claims()) == 1

    def test_acquire_same_agent_no_conflict(self) -> None:
        mgr = ClaimManager()
        c1 = _make_claim(agent_id="a", paths=["src/*.py"])
        c2 = _make_claim(agent_id="a", paths=["src/*.py"])
        assert mgr.acquire(c1) is None
        assert mgr.acquire(c2) is None
        assert len(mgr.get_active_claims()) == 2

    def test_acquire_different_paths_no_conflict(self) -> None:
        mgr = ClaimManager()
        c1 = _make_claim(agent_id="a", paths=["src/api/*.py"])
        c2 = _make_claim(agent_id="b", paths=["src/db/*.py"])
        assert mgr.acquire(c1) is None
        assert mgr.acquire(c2) is None

    def test_release(self) -> None:
        mgr = ClaimManager()
        claim = _make_claim()
        mgr.acquire(claim)
        mgr.release(str(claim.claim_id))
        assert len(mgr.get_active_claims()) == 0

    def test_release_nonexistent_is_noop(self) -> None:
        mgr = ClaimManager()
        mgr.release("does-not-exist")  # should not raise


# ---------------------------------------------------------------------------
# Overlap detection
# ---------------------------------------------------------------------------

class TestOverlapDetection:
    def test_exact_glob_overlap(self) -> None:
        mgr = ClaimManager()
        c1 = _make_claim(paths=["src/**/*.py"])
        mgr.acquire(c1)
        overlapping = mgr.check_overlap(["src/**/*.py"])
        assert len(overlapping) == 1
        assert overlapping[0].claim_id == c1.claim_id

    def test_nested_glob_overlap(self) -> None:
        mgr = ClaimManager()
        c1 = _make_claim(paths=["src/**/*.py"])
        mgr.acquire(c1)
        # A more specific path matches the broader glob
        overlapping = mgr.check_overlap(["src/api/routes.py"])
        assert len(overlapping) == 1

    def test_no_overlap(self) -> None:
        mgr = ClaimManager()
        c1 = _make_claim(paths=["src/api/*.py"])
        mgr.acquire(c1)
        overlapping = mgr.check_overlap(["tests/*.py"])
        assert len(overlapping) == 0


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

class TestConflictResolution:
    def test_higher_priority_new_claim_wins(self) -> None:
        mgr = ClaimManager()
        c1 = _make_claim(agent_id="a", paths=["src/*.py"], priority=1)
        c2 = _make_claim(agent_id="b", paths=["src/*.py"], priority=10)
        mgr.acquire(c1)
        conflict = mgr.acquire(c2)
        # Higher priority new claim should displace existing
        assert conflict is None
        active = mgr.get_active_claims()
        assert len(active) == 1
        assert active[0].agent_id == "b"

    def test_lower_priority_new_claim_blocked(self) -> None:
        mgr = ClaimManager()
        c1 = _make_claim(agent_id="a", paths=["src/*.py"], priority=10)
        c2 = _make_claim(agent_id="b", paths=["src/*.py"], priority=1)
        mgr.acquire(c1)
        conflict = mgr.acquire(c2)
        assert conflict is not None
        assert conflict.resolution == ConflictResolution.EXISTING_WINS

    def test_equal_priority_existing_wins_fifo(self) -> None:
        mgr = ClaimManager()
        c1 = _make_claim(agent_id="a", paths=["src/*.py"], priority=5)
        c2 = _make_claim(agent_id="b", paths=["src/*.py"], priority=5)
        mgr.acquire(c1)
        conflict = mgr.acquire(c2)
        assert conflict is not None
        assert conflict.resolution == ConflictResolution.EXISTING_WINS


# ---------------------------------------------------------------------------
# Claim expiry
# ---------------------------------------------------------------------------

class TestClaimExpiry:
    def test_expired_claim_is_removed(self) -> None:
        mgr = ClaimManager()
        claim = _make_expired_claim(agent_id="a", paths=["src/*.py"])
        # Manually insert (bypass acquire's expiry sweep timing)
        mgr._claims[str(claim.claim_id)] = claim
        assert len(mgr.get_active_claims()) == 0

    def test_expired_claim_does_not_block(self) -> None:
        mgr = ClaimManager()
        expired = _make_expired_claim(agent_id="a", paths=["src/*.py"])
        mgr._claims[str(expired.claim_id)] = expired
        fresh = _make_claim(agent_id="b", paths=["src/*.py"])
        assert mgr.acquire(fresh) is None


# ---------------------------------------------------------------------------
# DeployQueue
# ---------------------------------------------------------------------------

class TestDeployQueue:
    def _enqueue_helper(self, q: DeployQueue, intent_id: str, priority: int) -> int:
        claim = _make_claim()
        return q.enqueue(intent_id, priority, claim)

    def test_ordering_by_priority(self) -> None:
        q = DeployQueue()
        self._enqueue_helper(q, "low", 1)
        self._enqueue_helper(q, "high", 10)
        self._enqueue_helper(q, "mid", 5)
        assert q.dequeue() == "high"
        assert q.dequeue() == "mid"
        assert q.dequeue() == "low"

    def test_dequeue_empty(self) -> None:
        q = DeployQueue()
        assert q.dequeue() is None

    def test_peek(self) -> None:
        q = DeployQueue()
        self._enqueue_helper(q, "a", 5)
        assert q.peek() == "a"
        # peek should not remove the entry
        assert q.peek() == "a"

    def test_reorder(self) -> None:
        q = DeployQueue()
        self._enqueue_helper(q, "a", 1)
        self._enqueue_helper(q, "b", 5)
        # Promote 'a' above 'b'
        q.reorder("a", 10)
        assert q.dequeue() == "a"

    def test_reorder_nonexistent_raises(self) -> None:
        q = DeployQueue()
        with pytest.raises(KeyError):
            q.reorder("ghost", 1)

    def test_remove(self) -> None:
        q = DeployQueue()
        self._enqueue_helper(q, "a", 5)
        self._enqueue_helper(q, "b", 3)
        q.remove("a")
        assert q.dequeue() == "b"
        assert q.dequeue() is None

    def test_list_queue(self) -> None:
        q = DeployQueue()
        self._enqueue_helper(q, "a", 1)
        self._enqueue_helper(q, "b", 10)
        self._enqueue_helper(q, "c", 5)
        entries = q.list_queue()
        ids = [e.intent_id for e in entries]
        assert ids == ["b", "c", "a"]

    def test_enqueue_returns_position(self) -> None:
        q = DeployQueue()
        pos1 = self._enqueue_helper(q, "a", 10)
        assert pos1 == 1
        pos2 = self._enqueue_helper(q, "b", 1)
        assert pos2 == 2


# ---------------------------------------------------------------------------
# SemanticMergeChecker
# ---------------------------------------------------------------------------

class TestSemanticMergeChecker:
    def test_disjoint_files_compatible(self) -> None:
        checker = SemanticMergeChecker()
        a = Change(intent_id="i1", files=["src/api.py"])
        b = Change(intent_id="i2", files=["src/db.py"])
        result = checker.check(a, b)
        assert result.compatible is True
        assert result.auto_resolvable is True
        assert result.conflicts == []

    def test_overlapping_files_incompatible(self) -> None:
        checker = SemanticMergeChecker()
        a = Change(intent_id="i1", files=["src/api.py", "src/models.py"])
        b = Change(intent_id="i2", files=["src/models.py", "src/db.py"])
        result = checker.check(a, b)
        assert result.compatible is False
        assert result.auto_resolvable is False
        assert len(result.conflicts) == 1
        assert "src/models.py" in result.conflicts[0]

    def test_suggest_order_by_priority_then_time(self) -> None:
        checker = SemanticMergeChecker()
        c1 = _make_claim(priority=1)
        c2 = _make_claim(priority=10)
        c3 = _make_claim(priority=5)
        ordered = checker.suggest_order([c1, c2, c3])
        priorities = [c.priority for c in ordered]
        assert priorities == [10, 5, 1]
