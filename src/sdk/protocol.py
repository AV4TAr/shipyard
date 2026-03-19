"""Protocol models — the contract between agents and the Shipyard system.

These Pydantic v2 models define the exact shape of data exchanged between
external AI agents and the system over the SDK API.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AgentRegistration(BaseModel):
    """An agent registering itself with the system."""

    agent_id: str
    name: str
    capabilities: list[str]  # e.g. ["python", "frontend", "api", "security"]
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = 1


class TaskAssignment(BaseModel):
    """A task assigned to an agent."""

    task_id: uuid.UUID
    goal_id: uuid.UUID
    title: str
    description: str
    constraints: list[str]
    acceptance_criteria: list[str]
    target_files: list[str] = Field(default_factory=list)
    estimated_risk: str  # "low", "medium", "high", "critical"

    # Lease fields (populated when claimed via LeaseManager)
    lease_expires_at: Optional[datetime] = None
    lease_duration_seconds: Optional[int] = None
    heartbeat_interval_seconds: Optional[int] = None

    # Worktree fields (populated when project has a repo)
    worktree_path: Optional[str] = None
    branch_name: Optional[str] = None


class WorkSubmission(BaseModel):
    """An agent submitting completed work."""

    task_id: uuid.UUID
    agent_id: str
    intent_id: uuid.UUID
    diff: Optional[str] = None  # unified diff (optional when using worktrees)
    description: str
    test_command: str = "pytest"
    files_changed: list[str] = Field(default_factory=list)


class HeartbeatRequest(BaseModel):
    """Agent heartbeat to renew a task lease."""

    agent_id: str
    phase: Optional[str] = None  # AgentPhase value


class HeartbeatResponse(BaseModel):
    """Response to a heartbeat request."""

    task_id: uuid.UUID
    lease_expires_at: datetime
    lease_duration_seconds: int
    acknowledged: bool = True
    cancel: bool = False


class FeedbackMessage(BaseModel):
    """Structured feedback sent back to the agent."""

    task_id: uuid.UUID
    status: str  # "accepted", "rejected", "needs_revision"
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)
    validation_results: dict[str, Any] = Field(default_factory=dict)
