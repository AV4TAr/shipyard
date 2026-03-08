"""Human-readable formatting of pipeline events."""

from .models import Event

_EVENT_LABELS: dict[str, str] = {
    "pipeline.started": "Pipeline started",
    "pipeline.passed": "Pipeline passed",
    "pipeline.failed": "Pipeline failed",
    "approval.needed": "Approval needed",
    "approval.granted": "Approval granted",
    "approval.rejected": "Approval rejected",
    "goal.created": "Goal created",
    "goal.activated": "Goal activated",
    "goal.completed": "Goal completed",
    "agent.registered": "Agent registered",
    "deploy.started": "Deploy started",
    "deploy.completed": "Deploy completed",
    "anomaly.detected": "Anomaly detected",
}


def format_event_summary(event: Event) -> str:
    """Return a one-line human-readable summary of *event*."""
    label = _EVENT_LABELS.get(event.event_type.value, event.event_type.value)
    parts = [f"[{label}]"]

    # Include a few key data fields inline when present.
    for key in ("goal_id", "pipeline_id", "agent_id", "reason", "title"):
        if key in event.data:
            parts.append(f"{key}={event.data[key]}")

    parts.append(f"({event.source})")
    return " ".join(parts)


def format_event_detail(event: Event) -> str:
    """Return a multi-line detailed view of *event*."""
    label = _EVENT_LABELS.get(event.event_type.value, event.event_type.value)
    lines = [
        f"Event: {label}",
        f"Type:  {event.event_type.value}",
        f"Time:  {event.timestamp.isoformat()}",
        f"Source: {event.source}",
    ]

    if event.data:
        lines.append("Data:")
        for key, value in event.data.items():
            lines.append(f"  {key}: {value}")

    return "\n".join(lines)
