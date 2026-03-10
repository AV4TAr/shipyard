"""Pydantic models for the Trust & Risk Scoring system."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field, computed_field


class RiskLevel(str, enum.Enum):
    """Risk classification for a change."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DeployRoute(str, enum.Enum):
    """Deployment routing based on risk assessment."""

    AUTO_DEPLOY = "auto_deploy"
    AGENT_REVIEW = "agent_review"
    HUMAN_APPROVAL = "human_approval"
    HUMAN_APPROVAL_CANARY = "human_approval_canary"


class RiskFactor(BaseModel):
    """A single factor contributing to the overall risk score."""

    name: str
    weight: float = Field(ge=0.0, le=1.0)
    score: float = Field(ge=0.0, le=1.0)
    description: str


class AgentProfile(BaseModel):
    """Tracks an agent's deployment history and trust level."""

    agent_id: str
    total_deployments: int = 0
    successful_deployments: int = 0
    rollbacks: int = 0
    avg_risk_score: float = 0.0
    domain_scores: dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trust_score(self) -> float:
        """Computed trust score in [0, 1] based on deployment history.

        Formula: success_rate * 0.6 + (1 - rollback_rate) * 0.3 + tenure_bonus * 0.1
        New agents start with a low trust score.
        """
        if self.total_deployments == 0:
            return 0.1  # baseline trust for brand-new agents

        sr = self.success_rate
        rollback_rate = self.rollbacks / self.total_deployments
        # Tenure bonus: ramps from 0 to 1 over 100 deployments
        tenure_bonus = min(self.total_deployments / 100.0, 1.0)

        score = sr * 0.6 + (1.0 - rollback_rate) * 0.3 + tenure_bonus * 0.1
        return round(min(max(score, 0.0), 1.0), 4)

    @property
    def success_rate(self) -> float:
        """Ratio of successful deployments to total deployments."""
        if self.total_deployments == 0:
            return 0.0
        return self.successful_deployments / self.total_deployments


class RiskAssessment(BaseModel):
    """The computed risk for a specific change."""

    intent_id: uuid.UUID
    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=1.0)
    factors: list[RiskFactor] = Field(default_factory=list)
    recommended_route: DeployRoute
