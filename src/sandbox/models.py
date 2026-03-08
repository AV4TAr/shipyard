"""Pydantic models for the Sandbox Execution Layer."""

from __future__ import annotations

import enum
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field


class SandboxStatus(str, enum.Enum):
    """Lifecycle status of a sandbox environment."""

    CREATING = "creating"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    DESTROYED = "destroyed"


class ResourceLimits(BaseModel):
    """Resource constraints for a sandbox environment."""

    max_cpu: float = Field(default=1.0, description="Maximum CPU cores")
    max_memory_mb: int = Field(default=512, description="Maximum memory in MB")
    max_disk_mb: int = Field(default=1024, description="Maximum disk space in MB")


class ResourceUsage(BaseModel):
    """Observed resource consumption during a sandbox run."""

    peak_cpu: float = 0.0
    peak_memory_mb: int = 0
    disk_used_mb: int = 0


class SandboxConfig(BaseModel):
    """Configuration for creating a sandbox environment."""

    intent_id: uuid.UUID = Field(description="Links this sandbox to an intent declaration")
    image: str = Field(default="python:3.11-slim", description="Base Docker image")
    timeout_seconds: int = Field(default=300, ge=1, description="Maximum execution time")
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    env_vars: dict[str, str] = Field(default_factory=dict)
    ports: list[int] = Field(default_factory=list)


class TestFailure(BaseModel):
    """A single test failure with machine-readable detail for agent consumption."""

    test_name: str
    message: str
    structured_error: dict[str, Any] = Field(
        default_factory=dict,
        description="Machine-readable error information for agents to consume",
    )


class TestResults(BaseModel):
    """Aggregated test results from a sandbox run."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[TestFailure] = Field(default_factory=list)


class SandboxResult(BaseModel):
    """Outcome of executing a command in a sandbox."""

    sandbox_id: uuid.UUID
    intent_id: uuid.UUID
    status: SandboxStatus
    logs: str = ""
    test_results: Optional[TestResults] = None
    duration_seconds: float = 0.0
    resource_usage: ResourceUsage = Field(default_factory=ResourceUsage)
