"""Pydantic models for the Multi-Signal Validation Framework."""

from __future__ import annotations

import enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ValidationSignal(str, enum.Enum):
    """Types of validation signals that can be run against agent work."""

    STATIC_ANALYSIS = "static_analysis"
    BEHAVIORAL_DIFF = "behavioral_diff"
    INTENT_ALIGNMENT = "intent_alignment"
    RESOURCE_BOUNDS = "resource_bounds"
    SECURITY_SCAN = "security_scan"


class Severity(str, enum.Enum):
    """Severity level for a validation finding."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Finding(BaseModel):
    """A single finding produced by a validation signal."""

    severity: Severity
    title: str
    description: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    suggestion: Optional[str] = Field(
        default=None,
        description="Machine-readable fix suggestion for agents",
    )


class SignalResult(BaseModel):
    """Result from running a single validation signal."""

    signal: ValidationSignal
    passed: bool
    confidence: float = Field(ge=0.0, le=1.0)
    findings: list[Finding] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0.0)


class ValidationVerdict(BaseModel):
    """Aggregate result from all validation signals."""

    intent_id: str
    signals: list[SignalResult] = Field(default_factory=list)
    overall_passed: bool
    risk_score: float = Field(ge=0.0, le=1.0)
    blocking_findings: list[Finding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
