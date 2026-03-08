"""Webhook notification system for AI-CICD pipeline events."""

from .channels import format_slack_message, send_slack, send_webhook
from .dispatcher import EventDispatcher
from .formatters import format_event_detail, format_event_summary
from .models import (
    Event,
    EventType,
    NotificationResult,
    SlackConfig,
    WebhookConfig,
)

__all__ = [
    "Event",
    "EventDispatcher",
    "EventType",
    "NotificationResult",
    "SlackConfig",
    "WebhookConfig",
    "format_event_detail",
    "format_event_summary",
    "format_slack_message",
    "send_slack",
    "send_webhook",
]
