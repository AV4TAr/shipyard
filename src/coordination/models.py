"""Pydantic models for the Agent Coordination Layer."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ConflictResolution(str, enum.Enum):
    """How a conflict between two claims should be resolved."""

    NEW_WINS = "new_wins"  # Higher-priority new claim displaces existing
    EXISTING_WINS = "existing_wins"  # Existing claim keeps its lock
    QUEUE = "queue"  # New claim is queued behind existing
    REJECT = "reject"  # New claim is outright rejected


class Claim(BaseModel):
    """An agent's lock on a set of code paths / services."""

    claim_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    agent_id: str
    intent_id: uuid.UUID
    paths: list[str] = Field(
        default_factory=list,
        description="File-path globs this claim covers (e.g. 'src/api/**/*.py').",
    )
    services: list[str] = Field(default_factory=list)
    acquired_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    expires_at: datetime
    priority: int = Field(
        default=0,
        description="Higher value = more important.",
    )


class ClaimConflict(BaseModel):
    """Describes a conflict between two claims."""

    existing_claim: Claim
    new_claim: Claim
    overlapping_paths: list[str] = Field(default_factory=list)
    resolution: ConflictResolution


class MergeCheck(BaseModel):
    """Result of checking whether two changes are semantically compatible."""

    compatible: bool
    conflicts: list[str] = Field(default_factory=list)
    auto_resolvable: bool = False
