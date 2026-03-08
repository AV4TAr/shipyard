"""Individual notification channel implementations."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request

from .models import Event, EventType, NotificationResult, SlackConfig, WebhookConfig


def send_webhook(config: WebhookConfig, event: Event) -> NotificationResult:
    """Send event to a generic webhook endpoint.

    Posts a JSON payload to ``config.url``.  Custom headers from
    ``config.headers`` are included.  If ``config.secret`` is set an
    ``X-Signature`` header containing an HMAC-SHA256 hex digest is added.

    All HTTP and connection errors are caught and returned as a
    :class:`NotificationResult` with ``success=False``.
    """
    try:
        payload = json.dumps(event.model_dump(mode="json")).encode("utf-8")

        req = urllib.request.Request(
            config.url,
            data=payload,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")

        for key, value in config.headers.items():
            req.add_header(key, value)

        if config.secret:
            signature = hmac.new(
                config.secret.encode("utf-8"),
                payload,
                hashlib.sha256,
            ).hexdigest()
            req.add_header("X-Signature", signature)

        with urllib.request.urlopen(req) as response:
            return NotificationResult(
                success=True,
                status_code=response.status,
            )
    except urllib.error.HTTPError as exc:
        return NotificationResult(
            success=False,
            status_code=exc.code,
            error=str(exc),
        )
    except Exception as exc:
        return NotificationResult(
            success=False,
            error=str(exc),
        )


def send_slack(config: SlackConfig, event: Event) -> NotificationResult:
    """Send event to Slack via incoming webhook.

    Formats the event using :func:`format_slack_message` and posts the
    resulting Block Kit payload to ``config.webhook_url``.
    """
    try:
        slack_payload = format_slack_message(event)

        if config.channel:
            slack_payload["channel"] = config.channel

        payload = json.dumps(slack_payload).encode("utf-8")

        req = urllib.request.Request(
            config.webhook_url,
            data=payload,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req) as response:
            return NotificationResult(
                success=True,
                status_code=response.status,
            )
    except urllib.error.HTTPError as exc:
        return NotificationResult(
            success=False,
            status_code=exc.code,
            error=str(exc),
        )
    except Exception as exc:
        return NotificationResult(
            success=False,
            error=str(exc),
        )


_EVENT_COLORS: dict[str, str] = {
    # Green — success events
    EventType.PIPELINE_PASSED: "#36a64f",
    EventType.GOAL_COMPLETED: "#36a64f",
    EventType.DEPLOY_COMPLETED: "#36a64f",
    EventType.APPROVAL_GRANTED: "#36a64f",
    # Red — failure / alert events
    EventType.PIPELINE_FAILED: "#e01e5a",
    EventType.APPROVAL_REJECTED: "#e01e5a",
    EventType.ANOMALY_DETECTED: "#e01e5a",
    # Yellow — attention events
    EventType.APPROVAL_NEEDED: "#ecb22e",
    EventType.PIPELINE_STARTED: "#ecb22e",
    EventType.DEPLOY_STARTED: "#ecb22e",
    # Blue — informational
    EventType.GOAL_CREATED: "#2ea4ec",
    EventType.GOAL_ACTIVATED: "#2ea4ec",
    EventType.AGENT_REGISTERED: "#2ea4ec",
}


def format_slack_message(event: Event) -> dict:
    """Format an event as a Slack Block Kit message.

    Returns a dict containing ``blocks`` suitable for posting to a Slack
    incoming webhook.  Uses colour-coded context lines: green for success,
    red for failure, yellow for approvals / attention items.
    """
    color = _EVENT_COLORS.get(event.event_type, "#cccccc")
    event_label = event.event_type.value.replace(".", " ").title()

    data_fields = []
    for key, value in event.data.items():
        data_fields.append({
            "type": "mrkdwn",
            "text": f"*{key}:* {value}",
        })

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": event_label,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Source:* {event.source}",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f":clock1: {event.timestamp.isoformat()}",
                },
            ],
        },
    ]

    if data_fields:
        blocks.append({
            "type": "section",
            "fields": data_fields,
        })

    return {
        "blocks": blocks,
        "attachments": [{"color": color, "blocks": []}],
    }
