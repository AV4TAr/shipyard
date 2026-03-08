"""Pydantic models for the Pipeline Orchestrator."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class PipelineStage(str, enum.Enum):
    """Ordered stages that every pipeline run passes through."""

    INTENT = "intent"
    SANDBOX = "sandbox"
    VALIDATION = "validation"
    TRUST_ROUTING = "trust_routing"
    DEPLOY = "deploy"
    MONITORING = "monitoring"


class PipelineStatus(str, enum.Enum):
    """Status of a pipeline run or individual stage."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    ROLLED_BACK = "rolled_back"


class StageResult(BaseModel):
    """Outcome of a single pipeline stage."""

    stage: PipelineStage
    status: PipelineStatus
    duration_seconds: float = 0.0
    output: dict[str, Any] = Field(
        default_factory=dict,
        description="Machine-readable output for the agent to consume.",
    )
    error: Optional[str] = None


class PipelineRun(BaseModel):
    """Tracks a full pipeline execution from intent to deploy."""

    run_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    intent_id: Optional[uuid.UUID] = None
    agent_id: str = ""
    current_stage: PipelineStage = PipelineStage.INTENT
    status: PipelineStatus = PipelineStatus.PENDING
    stage_results: dict[PipelineStage, StageResult] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def record_stage(self, result: StageResult) -> None:
        """Record the result of a stage and update pipeline state accordingly."""
        self.stage_results[result.stage] = result
        self.current_stage = result.stage

        if result.status == PipelineStatus.FAILED:
            self.status = PipelineStatus.FAILED
            self.completed_at = datetime.now(timezone.utc)
        elif result.status == PipelineStatus.BLOCKED:
            self.status = PipelineStatus.BLOCKED

    def mark_completed(self, status: PipelineStatus = PipelineStatus.PASSED) -> None:
        """Mark the pipeline as completed."""
        self.status = status
        self.completed_at = datetime.now(timezone.utc)


class PipelineConfig(BaseModel):
    """Configuration for the pipeline orchestrator."""

    max_sandbox_iterations: int = Field(
        default=5,
        ge=1,
        description="Maximum sandbox test-fix iterations before giving up.",
    )
    sandbox_timeout: int = Field(
        default=300,
        ge=1,
        description="Maximum seconds for the sandbox execution.",
    )
    risk_thresholds: dict[str, float] = Field(
        default_factory=lambda: {
            "low": 0.25,
            "medium": 0.50,
            "high": 0.75,
            "critical": 1.0,
        },
        description="Mapping of RiskLevel value to upper-bound score.",
    )
    signal_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "static_analysis": 1.0,
            "behavioral_diff": 1.5,
            "intent_alignment": 1.2,
            "resource_bounds": 1.0,
            "security_scan": 2.0,
        },
        description="Weights for each validation signal when computing risk.",
    )
    auto_rollback_enabled: bool = Field(
        default=True,
        description="Whether to automatically roll back on anomaly detection.",
    )
