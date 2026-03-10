"""Event and notification models for the webhook notification system."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events that can trigger notifications."""

    PIPELINE_STARTED = "pipeline.started"
    PIPELINE_PASSED = "pipeline.passed"
    PIPELINE_FAILED = "pipeline.failed"
    APPROVAL_NEEDED = "approval.needed"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    GOAL_CREATED = "goal.created"
    GOAL_ACTIVATED = "goal.activated"
    GOAL_COMPLETED = "goal.completed"
    AGENT_REGISTERED = "agent.registered"
    DEPLOY_STARTED = "deploy.started"
    DEPLOY_COMPLETED = "deploy.completed"
    TASK_ROUTED = "task.routed"
    ROUTING_FALLBACK = "routing.fallback"
    ANOMALY_DETECTED = "anomaly.detected"


class Event(BaseModel):
    """An event that occurred in the AI-CICD pipeline."""

    event_type: EventType
    timestamp: datetime
    data: dict[str, Any] = Field(default_factory=dict)
    source: str = "ai-cicd"


class WebhookConfig(BaseModel):
    """Configuration for a generic webhook notification channel."""

    url: str
    events: list[EventType]
    headers: dict[str, str] = Field(default_factory=dict)
    secret: Optional[str] = None
    active: bool = True


class SlackConfig(BaseModel):
    """Configuration for a Slack incoming webhook notification channel."""

    webhook_url: str
    channel: Optional[str] = None
    events: list[EventType]
    active: bool = True


class NotificationResult(BaseModel):
    """Result of sending a notification."""

    success: bool
    status_code: Optional[int] = None
    error: Optional[str] = None
