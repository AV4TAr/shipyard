"""Pydantic v2 models for the Agent Selection & Routing System."""

from __future__ import annotations

import enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.intent.schema import RiskLevel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentCapability(str, enum.Enum):
    """What an agent specializes in."""

    FRONTEND = "frontend"
    BACKEND = "backend"
    DATA = "data"
    SECURITY = "security"
    MOBILE = "mobile"
    QA = "qa"
    DEVOPS = "devops"
    DOCUMENTATION = "documentation"
    FULLSTACK = "fullstack"
    GENERIC = "generic"


class AgentStatus(str, enum.Enum):
    """Current operational status of an agent."""

    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"
    PAUSED = "paused"


class TaskComplexity(str, enum.Enum):
    """Estimated complexity of a task."""

    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class RoutingStrategy(str, enum.Enum):
    """Strategy used to select an agent for a task."""

    BEST_MATCH = "best_match"
    ROUND_ROBIN = "round_robin"
    LEAST_LOADED = "least_loaded"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class AgentRegistration(BaseModel):
    """How an agent registers with the routing system."""

    agent_id: str
    name: str
    capabilities: list[AgentCapability]
    primary_capability: AgentCapability
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = 1
    status: AgentStatus = AgentStatus.AVAILABLE
    metadata: Optional[dict[str, Any]] = None


class TaskRequirements(BaseModel):
    """What a task needs from an agent, extracted from the task."""

    required_capabilities: list[AgentCapability] = Field(default_factory=list)
    preferred_capabilities: list[AgentCapability] = Field(default_factory=list)
    required_languages: list[str] = Field(default_factory=list)
    required_frameworks: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    estimated_complexity: TaskComplexity = TaskComplexity.SIMPLE


class RouteDecision(BaseModel):
    """The result of routing a task to an agent."""

    task_id: str
    selected_agent_id: Optional[str] = None
    agent_registration: Optional[AgentRegistration] = None
    match_score: float = Field(ge=0.0, le=1.0, default=0.0)
    match_reasons: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    alternatives: list[tuple[str, float]] = Field(default_factory=list)
