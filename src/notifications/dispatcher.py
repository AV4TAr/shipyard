"""Central event dispatcher for the notification system."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Callable, Coroutine

from .channels import send_slack, send_webhook
from .models import Event, NotificationResult, SlackConfig, WebhookConfig


class EventDispatcher:
    """Dispatches events to registered notification channels.

    Channels are added via :meth:`add_webhook` and :meth:`add_slack`.  When
    :meth:`dispatch` is called the event is sent only to channels whose
    configured event filters include the event's type.  Inactive channels
    are skipped.  Errors in individual channels are captured in the returned
    :class:`NotificationResult` list and never propagated.

    Live listeners (e.g. WebSocket broadcasters) can be registered via
    :meth:`add_live_listener` and will receive serialised event dicts
    whenever :meth:`dispatch` is called.
    """

    def __init__(self) -> None:
        self._webhooks: list[WebhookConfig] = []
        self._slack_configs: list[SlackConfig] = []
        self._history: list[tuple[Event, NotificationResult]] = []
        self._live_listeners: list[Callable[[dict[str, Any]], Coroutine]] = []
        self._event_buffer: deque[dict[str, Any]] = deque(maxlen=200)

    def add_webhook(self, config: WebhookConfig) -> None:
        """Register a generic webhook channel."""
        self._webhooks.append(config)

    def add_slack(self, config: SlackConfig) -> None:
        """Register a Slack webhook channel."""
        self._slack_configs.append(config)

    def remove_webhook(self, url: str) -> None:
        """Remove all webhook configs matching *url*."""
        self._webhooks = [w for w in self._webhooks if w.url != url]

    def add_live_listener(
        self, callback: Callable[[dict[str, Any]], Coroutine]
    ) -> None:
        """Register an async callback that receives every dispatched event."""
        self._live_listeners.append(callback)

    def remove_live_listener(
        self, callback: Callable[[dict[str, Any]], Coroutine]
    ) -> None:
        """Remove a previously registered live listener."""
        self._live_listeners = [cb for cb in self._live_listeners if cb is not callback]

    def get_recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent serialised events from the buffer."""
        items = list(self._event_buffer)
        return items[-limit:]

    def _serialise_event(self, event: Event) -> dict[str, Any]:
        """Convert an Event into a JSON-friendly dict for live consumers."""
        data = event.data or {}
        return {
            "type": "activity",
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "source": event.source,
            "agent_id": data.get("agent_id"),
            "description": data.get("description", ""),
            "metadata": {
                k: v for k, v in data.items()
                if k not in ("agent_id", "description")
            },
        }

    def _notify_live_listeners(self, serialised: dict[str, Any]) -> None:
        """Fire-and-forget broadcast to all live listeners."""
        for cb in self._live_listeners:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(cb(serialised))
            except RuntimeError:
                # No running event loop — skip async listeners
                pass

    def dispatch(self, event: Event) -> list[NotificationResult]:
        """Send *event* to all matching channels.

        Returns a list of :class:`NotificationResult` instances, one per
        channel that was attempted.  Events that do not match any channel's
        filter produce an empty list.
        """
        results: list[NotificationResult] = []

        # Buffer and broadcast to live listeners for every event
        serialised = self._serialise_event(event)
        self._event_buffer.append(serialised)
        self._notify_live_listeners(serialised)

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
