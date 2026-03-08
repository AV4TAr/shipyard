"""DeployQueue — priority queue of approved changes waiting to deploy."""

from __future__ import annotations

import heapq
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .models import Claim


class QueueEntry(BaseModel):
    """A single item in the deploy queue."""

    intent_id: str
    priority: int = Field(
        default=0,
        description="Higher value = deployed sooner.",
    )
    enqueued_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    claim: Claim


class DeployQueue:
    """Priority queue of approved changes awaiting deployment.

    Internally uses a min-heap; we negate priorities so that *higher*
    numeric priority is dequeued first.
    """

    def __init__(self) -> None:
        # Heap entries: (-priority, enqueued_at, intent_id)
        self._heap: list[tuple[int, datetime, str]] = []
        self._entries: dict[str, QueueEntry] = {}  # intent_id -> QueueEntry
        self._removed: set[str] = set()  # lazily-removed intent_ids

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, intent_id: str, priority: int, claim: Claim) -> int:
        """Add *intent_id* to the queue. Returns its 1-based position."""
        entry = QueueEntry(
            intent_id=intent_id,
            priority=priority,
            claim=claim,
        )
        self._entries[intent_id] = entry
        self._removed.discard(intent_id)
        heapq.heappush(self._heap, (-priority, entry.enqueued_at, intent_id))
        return self._position(intent_id)

    def dequeue(self) -> str | None:
        """Remove and return the highest-priority *intent_id*, or None."""
        while self._heap:
            _neg_pri, _ts, intent_id = heapq.heappop(self._heap)
            if intent_id in self._removed:
                self._removed.discard(intent_id)
                continue
            self._entries.pop(intent_id, None)
            return intent_id
        return None

    def peek(self) -> str | None:
        """Return the highest-priority *intent_id* without removing it."""
        while self._heap:
            _neg_pri, _ts, intent_id = self._heap[0]
            if intent_id in self._removed:
                heapq.heappop(self._heap)
                self._removed.discard(intent_id)
                continue
            return intent_id
        return None

    def reorder(self, intent_id: str, new_priority: int) -> None:
        """Change the priority of an enqueued item.

        Lazily marks the old heap entry as removed and pushes a new one.
        """
        if intent_id not in self._entries or intent_id in self._removed:
            raise KeyError(f"intent_id '{intent_id}' is not in the queue")
        entry = self._entries[intent_id]
        # Mark old entry stale
        self._removed.add(intent_id)
        # Re-insert with updated priority
        entry.priority = new_priority
        self._entries[intent_id] = entry
        self._removed.discard(intent_id)
        heapq.heappush(self._heap, (-new_priority, entry.enqueued_at, intent_id))

    def remove(self, intent_id: str) -> None:
        """Remove *intent_id* from the queue (lazy deletion)."""
        if intent_id in self._entries:
            self._removed.add(intent_id)
            self._entries.pop(intent_id, None)

    def list_queue(self) -> list[QueueEntry]:
        """Return all entries ordered by priority (highest first), then FIFO."""
        active = [
            e for e in self._entries.values() if e.intent_id not in self._removed
        ]
        return sorted(active, key=lambda e: (-e.priority, e.enqueued_at))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _position(self, intent_id: str) -> int:
        """1-based position of *intent_id* in the current ordering."""
        ordered = self.list_queue()
        for idx, entry in enumerate(ordered, start=1):
            if entry.intent_id == intent_id:
                return idx
        raise KeyError(f"intent_id '{intent_id}' is not in the queue")
