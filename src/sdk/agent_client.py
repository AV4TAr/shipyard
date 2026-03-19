"""Client-side SDK that agents use to interact with the Shipyard system.

Uses only stdlib (``urllib.request``) — no external HTTP dependencies.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import Any

from .protocol import (
    AgentRegistration,
    FeedbackMessage,
    TaskAssignment,
    WorkSubmission,
)


class SDKError(Exception):
    """Raised when an SDK API call fails."""

    def __init__(self, status_code: int, message: str, body: str = "") -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {message}")


class AgentClient:
    """SDK for AI agents to interact with the Shipyard system.

    Usage::

        client = AgentClient(
            base_url="http://localhost:8000",
            agent_id="my-agent",
            api_key="...",
        )
        client.register(name="My Agent", capabilities=["python", "api"])
        tasks = client.get_available_tasks()
        task = client.claim_task(task_id)
        # ... do work ...
        feedback = client.submit_work(submission)
    """

    def __init__(
        self,
        base_url: str,
        agent_id: str,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.api_key = api_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        *,
        name: str,
        capabilities: list[str],
        languages: list[str] | None = None,
        frameworks: list[str] | None = None,
        max_concurrent_tasks: int = 1,
    ) -> AgentRegistration:
        """Register this agent with the system."""
        registration = AgentRegistration(
            agent_id=self.agent_id,
            name=name,
            capabilities=capabilities,
            languages=languages or [],
            frameworks=frameworks or [],
            max_concurrent_tasks=max_concurrent_tasks,
        )
        data = self._post("/api/agents/sdk/register", registration.model_dump_json())
        return AgentRegistration.model_validate(data)

    def get_available_tasks(self) -> list[TaskAssignment]:
        """Get tasks available for this agent to claim."""
        data = self._get("/api/agents/sdk/tasks")
        return [TaskAssignment.model_validate(t) for t in data]

    def claim_task(self, task_id: uuid.UUID) -> TaskAssignment:
        """Claim a task for this agent to work on."""
        data = self._post(f"/api/agents/sdk/tasks/{task_id}/claim", "{}")
        return TaskAssignment.model_validate(data)

    def submit_work(self, submission: WorkSubmission) -> FeedbackMessage:
        """Submit completed work for pipeline validation."""
        data = self._post(
            f"/api/agents/sdk/tasks/{submission.task_id}/submit",
            submission.model_dump_json(),
        )
        return FeedbackMessage.model_validate(data)

    def get_feedback(self, task_id: uuid.UUID) -> FeedbackMessage | None:
        """Get feedback for a submitted task."""
        try:
            data = self._get(f"/api/agents/sdk/tasks/{task_id}/feedback")
        except SDKError as exc:
            if exc.status_code == 404:
                return None
            raise
        return FeedbackMessage.model_validate(data)

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build common request headers."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Agent-ID": self.agent_id,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _get(self, path: str) -> Any:
        """Issue a GET request and return parsed JSON."""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        return self._do_request(req)

    def _post(self, path: str, body: str) -> Any:
        """Issue a POST request with a JSON body and return parsed JSON."""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        return self._do_request(req)

    def _do_request(self, req: urllib.request.Request) -> Any:
        """Execute a request and handle errors."""
        try:
            with urllib.request.urlopen(req) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            body = ""
            if exc.fp:
                body = exc.fp.read().decode("utf-8", errors="replace")
            raise SDKError(exc.code, exc.reason, body) from exc
        except urllib.error.URLError as exc:
            raise SDKError(0, str(exc.reason)) from exc
