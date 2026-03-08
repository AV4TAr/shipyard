"""Central event dispatcher for the notification system."""

from __future__ import annotations

from .channels import send_slack, send_webhook
from .models import Event, NotificationResult, SlackConfig, WebhookConfig


class EventDispatcher:
    """Dispatches events to registered notification channels.

    Channels are added via :meth:`add_webhook` and :meth:`add_slack`.  When
    :meth:`dispatch` is called the event is sent only to channels whose
    configured event filters include the event's type.  Inactive channels
    are skipped.  Errors in individual channels are captured in the returned
    :class:`NotificationResult` list and never propagated.
    """

    def __init__(self) -> None:
        self._webhooks: list[WebhookConfig] = []
        self._slack_configs: list[SlackConfig] = []
        self._history: list[tuple[Event, NotificationResult]] = []

    def add_webhook(self, config: WebhookConfig) -> None:
        """Register a generic webhook channel."""
        self._webhooks.append(config)

    def add_slack(self, config: SlackConfig) -> None:
        """Register a Slack webhook channel."""
        self._slack_configs.append(config)

    def remove_webhook(self, url: str) -> None:
        """Remove all webhook configs matching *url*."""
        self._webhooks = [w for w in self._webhooks if w.url != url]

    def dispatch(self, event: Event) -> list[NotificationResult]:
        """Send *event* to all matching channels.

        Returns a list of :class:`NotificationResult` instances, one per
        channel that was attempted.  Events that do not match any channel's
        filter produce an empty list.
        """
        results: list[NotificationResult] = []

        for wh in self._webhooks:
            if not wh.active:
                continue
            if event.event_type not in wh.events:
                continue
            try:
                result = send_webhook(wh, event)
            except Exception as exc:
                result = NotificationResult(success=False, error=str(exc))
            results.append(result)
            self._history.append((event, result))

        for sc in self._slack_configs:
            if not sc.active:
                continue
            if event.event_type not in sc.events:
                continue
            try:
                result = send_slack(sc, event)
            except Exception as exc:
                result = NotificationResult(success=False, error=str(exc))
            results.append(result)
            self._history.append((event, result))

        return results

    def get_history(self, limit: int = 50) -> list[tuple[Event, NotificationResult]]:
        """Return the most recent notification history entries."""
        return self._history[-limit:]
