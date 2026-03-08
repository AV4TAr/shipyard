"""Pydantic models for the Intent Declaration Layer."""

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class RiskLevel(str, enum.Enum):
    """Risk classification for an intent."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IntentDeclaration(BaseModel):
    """Declares what an agent intends to change and why."""

    agent_id: str
    intent_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    description: str
    rationale: str
    target_files: list[str]
    target_services: list[str] = Field(default_factory=list)
    risk_hints: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None


class ScopeConstraint(BaseModel):
    """Defines what an agent is allowed to touch."""

    agent_id: str
    allowed_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)
    allowed_services: list[str] = Field(default_factory=list)
    max_risk_level: RiskLevel = RiskLevel.HIGH


class IntentVerdict(BaseModel):
    """Result of validating an intent against scope constraints."""

    intent_id: uuid.UUID
    approved: bool
    risk_level: RiskLevel
    denial_reasons: list[str] = Field(default_factory=list)
    conflicts: list[uuid.UUID] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
