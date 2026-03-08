"""Tests for the webhook notification system."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from src.notifications.channels import format_slack_message, send_slack, send_webhook
from src.notifications.dispatcher import EventDispatcher
from src.notifications.formatters import format_event_detail, format_event_summary
from src.notifications.models import (
    Event,
    EventType,
    NotificationResult,
    SlackConfig,
    WebhookConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    event_type: EventType = EventType.PIPELINE_PASSED,
    data: dict | None = None,
) -> Event:
    return Event(
        event_type=event_type,
        timestamp=datetime(2026, 3, 7, 12, 0, 0, tzinfo=timezone.utc),
        data=data or {"pipeline_id": "abc-123"},
    )


def _mock_response(status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestEventType:
    def test_all_event_types_exist(self):
        expected = [
            "pipeline.started", "pipeline.passed", "pipeline.failed",
            "approval.needed", "approval.granted", "approval.rejected",
            "goal.created", "goal.activated", "goal.completed",
            "agent.registered", "deploy.started", "deploy.completed",
            "anomaly.detected",
        ]
        actual = [e.value for e in EventType]
        assert sorted(actual) == sorted(expected)

    def test_event_type_count(self):
        assert len(EventType) == 13


class TestEventModel:
    def test_creation(self):
        event = _make_event()
        assert event.event_type == EventType.PIPELINE_PASSED
        assert event.source == "ai-cicd"
        assert event.data["pipeline_id"] == "abc-123"

    def test_serialization_roundtrip(self):
        event = _make_event()
        dumped = event.model_dump(mode="json")
        restored = Event.model_validate(dumped)
        assert restored.event_type == event.event_type
        assert restored.timestamp == event.timestamp

    def test_default_data(self):
        event = Event(
            event_type=EventType.GOAL_CREATED,
            timestamp=datetime.now(tz=timezone.utc),
        )
        assert event.data == {}

    def test_custom_source(self):
        event = Event(
            event_type=EventType.GOAL_CREATED,
            timestamp=datetime.now(tz=timezone.utc),
            source="custom-agent",
        )
        assert event.source == "custom-agent"


class TestWebhookConfig:
    def test_creation(self):
        cfg = WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
        )
        assert cfg.url == "https://example.com/hook"
        assert cfg.active is True
        assert cfg.secret is None
        assert cfg.headers == {}

    def test_with_secret_and_headers(self):
        cfg = WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
            secret="s3cret",
            headers={"X-Custom": "val"},
        )
        assert cfg.secret == "s3cret"
        assert cfg.headers["X-Custom"] == "val"


class TestSlackConfig:
    def test_creation(self):
        cfg = SlackConfig(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            events=[EventType.PIPELINE_FAILED],
        )
        assert cfg.active is True
        assert cfg.channel is None

    def test_with_channel(self):
        cfg = SlackConfig(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            events=[EventType.PIPELINE_FAILED],
            channel="#ops",
        )
        assert cfg.channel == "#ops"


class TestNotificationResult:
    def test_success(self):
        r = NotificationResult(success=True, status_code=200)
        assert r.success
        assert r.error is None

    def test_failure(self):
        r = NotificationResult(success=False, error="timeout")
        assert not r.success
        assert r.error == "timeout"


# ---------------------------------------------------------------------------
# Channel tests
# ---------------------------------------------------------------------------

class TestSendWebhook:
    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_basic_post(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(200)

        cfg = WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
        )
        event = _make_event()
        result = send_webhook(cfg, event)

        assert result.success is True
        assert result.status_code == 200

        # Verify the request was constructed correctly.
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.full_url == "https://example.com/hook"
        assert req.get_header("Content-type") == "application/json"

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_custom_headers(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(200)

        cfg = WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
            headers={"X-Custom": "hello"},
        )
        result = send_webhook(cfg, _make_event())
        assert result.success

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("X-custom") == "hello"

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_hmac_signature(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(200)
        secret = "my-secret"

        cfg = WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
            secret=secret,
        )
        event = _make_event()
        result = send_webhook(cfg, event)
        assert result.success

        req = mock_urlopen.call_args[0][0]
        sig_header = req.get_header("X-signature")
        assert sig_header is not None

        # Recompute expected signature.
        payload = json.dumps(event.model_dump(mode="json")).encode("utf-8")
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert sig_header == expected

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            url="https://example.com/hook",
            code=500,
            msg="Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )
        cfg = WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
        )
        result = send_webhook(cfg, _make_event())
        assert result.success is False
        assert result.status_code == 500

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("Connection refused")
        cfg = WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
        )
        result = send_webhook(cfg, _make_event())
        assert result.success is False
        assert result.error is not None
        assert result.status_code is None


class TestSendSlack:
    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_sends_to_webhook_url(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(200)
        cfg = SlackConfig(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            events=[EventType.PIPELINE_FAILED],
        )
        result = send_slack(cfg, _make_event(EventType.PIPELINE_FAILED))
        assert result.success
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://hooks.slack.com/services/T/B/X"

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_includes_channel_override(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(200)
        cfg = SlackConfig(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            events=[EventType.PIPELINE_FAILED],
            channel="#alerts",
        )
        send_slack(cfg, _make_event(EventType.PIPELINE_FAILED))
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        assert body["channel"] == "#alerts"

    @patch("src.notifications.channels.urllib.request.urlopen")
    def test_slack_error_handled(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("DNS failure")
        cfg = SlackConfig(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            events=[EventType.PIPELINE_FAILED],
        )
        result = send_slack(cfg, _make_event())
        assert result.success is False
        assert "DNS failure" in result.error


class TestFormatSlackMessage:
    def test_returns_blocks(self):
        msg = format_slack_message(_make_event())
        assert "blocks" in msg
        assert isinstance(msg["blocks"], list)
        assert len(msg["blocks"]) >= 2

    def test_header_block(self):
        msg = format_slack_message(_make_event(EventType.PIPELINE_PASSED))
        header = msg["blocks"][0]
        assert header["type"] == "header"
        assert "Pipeline Passed" in header["text"]["text"]

    def test_different_event_types_different_messages(self):
        msg_pass = format_slack_message(_make_event(EventType.PIPELINE_PASSED))
        msg_fail = format_slack_message(_make_event(EventType.PIPELINE_FAILED))
        assert msg_pass["blocks"][0]["text"]["text"] != msg_fail["blocks"][0]["text"]["text"]

    def test_color_coding_success(self):
        msg = format_slack_message(_make_event(EventType.PIPELINE_PASSED))
        assert msg["attachments"][0]["color"] == "#36a64f"

    def test_color_coding_failure(self):
        msg = format_slack_message(_make_event(EventType.PIPELINE_FAILED))
        assert msg["attachments"][0]["color"] == "#e01e5a"

    def test_color_coding_attention(self):
        msg = format_slack_message(_make_event(EventType.APPROVAL_NEEDED))
        assert msg["attachments"][0]["color"] == "#ecb22e"

    def test_data_fields_included(self):
        event = _make_event(data={"goal_id": "g-1", "status": "ok"})
        msg = format_slack_message(event)
        # Data fields appear in a section block.
        fields_block = [b for b in msg["blocks"] if b.get("fields")]
        assert len(fields_block) == 1
        texts = [f["text"] for f in fields_block[0]["fields"]]
        assert any("goal_id" in t for t in texts)


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------

class TestFormatEventSummary:
    def test_contains_event_label(self):
        summary = format_event_summary(_make_event(EventType.PIPELINE_PASSED))
        assert "Pipeline passed" in summary

    def test_contains_source(self):
        summary = format_event_summary(_make_event())
        assert "(ai-cicd)" in summary

    def test_contains_key_data_fields(self):
        event = _make_event(data={"pipeline_id": "p-1", "agent_id": "a-2"})
        summary = format_event_summary(event)
        assert "pipeline_id=p-1" in summary
        assert "agent_id=a-2" in summary

    def test_single_line(self):
        summary = format_event_summary(_make_event())
        assert "\n" not in summary


class TestFormatEventDetail:
    def test_contains_event_info(self):
        detail = format_event_detail(_make_event(EventType.GOAL_CREATED))
        assert "Goal created" in detail
        assert "goal.created" in detail

    def test_contains_timestamp(self):
        detail = format_event_detail(_make_event())
        assert "2026-03-07" in detail

    def test_contains_data(self):
        event = _make_event(data={"reason": "tests failed"})
        detail = format_event_detail(event)
        assert "reason: tests failed" in detail

    def test_multi_line(self):
        detail = format_event_detail(_make_event())
        assert "\n" in detail


# ---------------------------------------------------------------------------
# Dispatcher tests
# ---------------------------------------------------------------------------

class TestEventDispatcher:
    def test_add_and_remove_webhook(self):
        d = EventDispatcher()
        cfg = WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
        )
        d.add_webhook(cfg)
        assert len(d._webhooks) == 1
        d.remove_webhook("https://example.com/hook")
        assert len(d._webhooks) == 0

    def test_remove_nonexistent_webhook_is_noop(self):
        d = EventDispatcher()
        d.remove_webhook("https://nowhere.com")  # should not raise

    @patch("src.notifications.dispatcher.send_webhook")
    def test_dispatch_routes_to_matching_webhook(self, mock_send):
        mock_send.return_value = NotificationResult(success=True, status_code=200)
        d = EventDispatcher()
        d.add_webhook(WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
        ))
        results = d.dispatch(_make_event(EventType.PIPELINE_PASSED))
        assert len(results) == 1
        assert results[0].success
        mock_send.assert_called_once()

    @patch("src.notifications.dispatcher.send_webhook")
    def test_dispatch_skips_non_matching_events(self, mock_send):
        d = EventDispatcher()
        d.add_webhook(WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
        ))
        results = d.dispatch(_make_event(EventType.PIPELINE_FAILED))
        assert len(results) == 0
        mock_send.assert_not_called()

    @patch("src.notifications.dispatcher.send_webhook")
    def test_dispatch_skips_inactive_webhook(self, mock_send):
        d = EventDispatcher()
        d.add_webhook(WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
            active=False,
        ))
        results = d.dispatch(_make_event(EventType.PIPELINE_PASSED))
        assert len(results) == 0

    @patch("src.notifications.dispatcher.send_webhook")
    def test_history_is_recorded(self, mock_send):
        mock_send.return_value = NotificationResult(success=True, status_code=200)
        d = EventDispatcher()
        d.add_webhook(WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
        ))
        d.dispatch(_make_event())
        history = d.get_history()
        assert len(history) == 1
        event, result = history[0]
        assert event.event_type == EventType.PIPELINE_PASSED
        assert result.success

    @patch("src.notifications.dispatcher.send_slack")
    def test_dispatch_routes_to_slack(self, mock_send):
        mock_send.return_value = NotificationResult(success=True, status_code=200)
        d = EventDispatcher()
        d.add_slack(SlackConfig(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            events=[EventType.ANOMALY_DETECTED],
        ))
        results = d.dispatch(_make_event(EventType.ANOMALY_DETECTED))
        assert len(results) == 1
        assert results[0].success

    @patch("src.notifications.dispatcher.send_slack")
    @patch("src.notifications.dispatcher.send_webhook")
    def test_mixed_webhook_and_slack(self, mock_wh, mock_slack):
        mock_wh.return_value = NotificationResult(success=True, status_code=200)
        mock_slack.return_value = NotificationResult(success=True, status_code=200)

        d = EventDispatcher()
        d.add_webhook(WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_FAILED],
        ))
        d.add_slack(SlackConfig(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            events=[EventType.PIPELINE_FAILED],
        ))

        results = d.dispatch(_make_event(EventType.PIPELINE_FAILED))
        assert len(results) == 2
        assert all(r.success for r in results)
        mock_wh.assert_called_once()
        mock_slack.assert_called_once()

    @patch("src.notifications.dispatcher.send_webhook")
    def test_channel_failure_does_not_crash_dispatcher(self, mock_send):
        mock_send.side_effect = RuntimeError("unexpected")
        d = EventDispatcher()
        d.add_webhook(WebhookConfig(
            url="https://example.com/hook",
            events=[EventType.PIPELINE_PASSED],
        ))
        # Should not raise
        results = d.dispatch(_make_event())
        assert len(results) == 1
        assert results[0].success is False

    def test_get_history_respects_limit(self):
        d = EventDispatcher()
        # Manually populate history.
        for i in range(10):
            d._history.append((
                _make_event(),
                NotificationResult(success=True, status_code=200),
            ))
        assert len(d.get_history(limit=3)) == 3
        assert len(d.get_history(limit=50)) == 10
