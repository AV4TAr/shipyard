"""Tests for the Agent SDK / Protocol.

Covers:
- Protocol model serialization / deserialization
- AgentClient HTTP request construction (mocked urllib)
- AgentClient error handling (404, 401, 500)
- FastAPI SDK routes via TestClient
- End-to-end flow: register -> get tasks -> claim -> submit -> feedback
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.cli.runtime import CLIRuntime
from src.sdk.agent_client import AgentClient, SDKError
from src.sdk.protocol import (
    AgentRegistration,
    FeedbackMessage,
    TaskAssignment,
    WorkSubmission,
)
from src.sdk.routes import _agent_registrations, _feedback_store, mount_sdk_routes

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runtime() -> CLIRuntime:
    """A fresh CLIRuntime with default in-memory components."""
    return CLIRuntime.from_defaults()


@pytest.fixture()
def app(runtime: CLIRuntime) -> FastAPI:
    """FastAPI app with SDK routes mounted."""
    _feedback_store.clear()
    _agent_registrations.clear()
    application = FastAPI()
    sdk_router = mount_sdk_routes(runtime)
    application.include_router(sdk_router)
    return application


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    """TestClient wired to the SDK routes."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Protocol model tests
# ---------------------------------------------------------------------------


class TestProtocolModels:
    """Verify all protocol models serialize and deserialize correctly."""

    def test_agent_registration_roundtrip(self) -> None:
        reg = AgentRegistration(
            agent_id="agent-1",
            name="Test Agent",
            capabilities=["python", "api"],
            languages=["python"],
            frameworks=["fastapi"],
            max_concurrent_tasks=3,
        )
        raw = reg.model_dump_json()
        restored = AgentRegistration.model_validate_json(raw)
        assert restored.agent_id == "agent-1"
        assert restored.capabilities == ["python", "api"]
        assert restored.max_concurrent_tasks == 3

    def test_task_assignment_roundtrip(self) -> None:
        tid = uuid.uuid4()
        gid = uuid.uuid4()
        ta = TaskAssignment(
            task_id=tid,
            goal_id=gid,
            title="Implement feature",
            description="Build the thing",
            constraints=["no breaking changes"],
            acceptance_criteria=["tests pass"],
            target_files=["src/foo.py"],
            estimated_risk="medium",
        )
        raw = ta.model_dump_json()
        restored = TaskAssignment.model_validate_json(raw)
        assert restored.task_id == tid
        assert restored.estimated_risk == "medium"

    def test_work_submission_roundtrip(self) -> None:
        ws = WorkSubmission(
            task_id=uuid.uuid4(),
            agent_id="agent-1",
            intent_id=uuid.uuid4(),
            diff="--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new",
            description="Fixed the bug",
            test_command="pytest tests/",
            files_changed=["foo.py"],
        )
        raw = ws.model_dump_json()
        restored = WorkSubmission.model_validate_json(raw)
        assert restored.agent_id == "agent-1"
        assert "old" in restored.diff

    def test_feedback_message_roundtrip(self) -> None:
        fm = FeedbackMessage(
            task_id=uuid.uuid4(),
            status="accepted",
            message="Pipeline passed",
            details={"run_id": "abc"},
            suggestions=["Deploy to staging first"],
            validation_results={"overall": True},
        )
        raw = fm.model_dump_json()
        restored = FeedbackMessage.model_validate_json(raw)
        assert restored.status == "accepted"
        assert restored.suggestions == ["Deploy to staging first"]

    def test_agent_registration_defaults(self) -> None:
        reg = AgentRegistration(
            agent_id="a",
            name="A",
            capabilities=[],
        )
        assert reg.languages == []
        assert reg.frameworks == []
        assert reg.max_concurrent_tasks == 1

    def test_feedback_message_defaults(self) -> None:
        fm = FeedbackMessage(
            task_id=uuid.uuid4(),
            status="rejected",
            message="nope",
        )
        assert fm.details == {}
        assert fm.suggestions == []
        assert fm.validation_results == {}


# ---------------------------------------------------------------------------
# AgentClient tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestAgentClient:
    """Verify AgentClient constructs correct HTTP requests."""

    def _mock_response(self, data: dict | list, status: int = 200) -> MagicMock:
        """Create a mock urllib response."""
        body = json.dumps(data).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_register_sends_post(self) -> None:
        sdk = AgentClient(base_url="http://localhost:8000", agent_id="a1", api_key="k")
        resp_data = {
            "agent_id": "a1",
            "name": "Agent 1",
            "capabilities": ["python"],
            "languages": [],
            "frameworks": [],
            "max_concurrent_tasks": 1,
        }
        with patch("urllib.request.urlopen", return_value=self._mock_response(resp_data)) as mock:
            result = sdk.register(name="Agent 1", capabilities=["python"])
            req = mock.call_args[0][0]
            assert req.get_method() == "POST"
            assert "/api/agents/sdk/register" in req.full_url
            assert req.get_header("Authorization") == "Bearer k"
            assert req.get_header("X-agent-id") == "a1"
            assert result.agent_id == "a1"

    def test_get_available_tasks_sends_get(self) -> None:
        sdk = AgentClient(base_url="http://localhost:8000", agent_id="a1")
        with patch("urllib.request.urlopen", return_value=self._mock_response([])) as mock:
            tasks = sdk.get_available_tasks()
            req = mock.call_args[0][0]
            assert req.get_method() == "GET"
            assert "/api/agents/sdk/tasks" in req.full_url
            assert tasks == []

    def test_claim_task_sends_post(self) -> None:
        sdk = AgentClient(base_url="http://localhost:8000", agent_id="a1")
        tid = uuid.uuid4()
        gid = uuid.uuid4()
        resp_data = {
            "task_id": str(tid),
            "goal_id": str(gid),
            "title": "T",
            "description": "D",
            "constraints": [],
            "acceptance_criteria": [],
            "estimated_risk": "low",
        }
        with patch("urllib.request.urlopen", return_value=self._mock_response(resp_data)):
            result = sdk.claim_task(tid)
            assert result.task_id == tid

    def test_submit_work_sends_post(self) -> None:
        sdk = AgentClient(base_url="http://localhost:8000", agent_id="a1")
        tid = uuid.uuid4()
        submission = WorkSubmission(
            task_id=tid,
            agent_id="a1",
            intent_id=uuid.uuid4(),
            diff="diff",
            description="desc",
        )
        resp_data = {
            "task_id": str(tid),
            "status": "accepted",
            "message": "ok",
        }
        with patch("urllib.request.urlopen", return_value=self._mock_response(resp_data)):
            result = sdk.submit_work(submission)
            assert result.status == "accepted"

    def test_get_feedback_returns_none_on_404(self) -> None:
        import urllib.error

        sdk = AgentClient(base_url="http://localhost:8000", agent_id="a1")
        err = urllib.error.HTTPError(
            "http://localhost:8000/api/agents/sdk/tasks/x/feedback",
            404,
            "Not Found",
            {},
            None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = sdk.get_feedback(uuid.uuid4())
            assert result is None

    def test_no_auth_header_without_api_key(self) -> None:
        sdk = AgentClient(base_url="http://localhost:8000", agent_id="a1")
        with patch("urllib.request.urlopen", return_value=self._mock_response([])) as mock:
            sdk.get_available_tasks()
            req = mock.call_args[0][0]
            assert req.get_header("Authorization") is None
            assert req.get_header("X-agent-id") == "a1"

    def test_sdk_error_on_401(self) -> None:
        import urllib.error

        sdk = AgentClient(base_url="http://localhost:8000", agent_id="a1", api_key="bad")
        err = urllib.error.HTTPError(
            "http://localhost:8000/api/agents/sdk/tasks",
            401,
            "Unauthorized",
            {},
            None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(SDKError) as exc_info:
                sdk.get_available_tasks()
            assert exc_info.value.status_code == 401

    def test_sdk_error_on_500(self) -> None:
        import urllib.error

        sdk = AgentClient(base_url="http://localhost:8000", agent_id="a1")
        err = urllib.error.HTTPError(
            "http://localhost:8000/api/agents/sdk/register",
            500,
            "Internal Server Error",
            {},
            None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(SDKError) as exc_info:
                sdk.register(name="X", capabilities=[])
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# SDK route tests (FastAPI TestClient)
# ---------------------------------------------------------------------------


class TestSDKRoutes:
    """Test SDK endpoints via FastAPI TestClient."""

    def test_register_agent(self, client: TestClient) -> None:
        resp = client.post(
            "/api/agents/sdk/register",
            json={
                "agent_id": "test-agent",
                "name": "Test Agent",
                "capabilities": ["python", "api"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "test-agent"
        assert data["capabilities"] == ["python", "api"]

    def test_list_tasks_empty(self, client: TestClient) -> None:
        resp = client.get("/api/agents/sdk/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_tasks_with_active_goal(
        self, client: TestClient, runtime: CLIRuntime
    ) -> None:
        goal = runtime.create_goal(
            title="Build widget",
            description="Create a widget service",
        )
        runtime.goal_manager.activate(goal.goal_id)

        resp = client.get("/api/agents/sdk/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert len(tasks) > 0
        assert tasks[0]["goal_id"] == str(goal.goal_id)

    def test_claim_task(self, client: TestClient, runtime: CLIRuntime) -> None:
        goal = runtime.create_goal(
            title="Fix bug", description="Fix the login bug"
        )
        breakdown = runtime.goal_manager.activate(goal.goal_id)
        task = breakdown.tasks[0]

        resp = client.post(f"/api/agents/sdk/tasks/{task.task_id}/claim")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == str(task.task_id)

    def test_claim_task_not_found(self, client: TestClient) -> None:
        fake_id = uuid.uuid4()
        resp = client.post(f"/api/agents/sdk/tasks/{fake_id}/claim")
        assert resp.status_code == 404

    def test_submit_work(self, client: TestClient, runtime: CLIRuntime) -> None:
        goal = runtime.create_goal(
            title="Add feature", description="Add rate limiting"
        )
        breakdown = runtime.goal_manager.activate(goal.goal_id)
        task = breakdown.tasks[0]

        # Claim first
        client.post(f"/api/agents/sdk/tasks/{task.task_id}/claim")

        intent_id = uuid.uuid4()
        resp = client.post(
            f"/api/agents/sdk/tasks/{task.task_id}/submit",
            json={
                "task_id": str(task.task_id),
                "agent_id": "test-agent",
                "intent_id": str(intent_id),
                "diff": "--- a/foo.py\n+++ b/foo.py\n",
                "description": "Added rate limiting",
                "files_changed": ["foo.py"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == str(task.task_id)
        assert data["status"] in ("accepted", "rejected", "needs_revision")

    def test_submit_work_not_found(self, client: TestClient) -> None:
        fake_id = uuid.uuid4()
        resp = client.post(
            f"/api/agents/sdk/tasks/{fake_id}/submit",
            json={
                "task_id": str(fake_id),
                "agent_id": "test-agent",
                "intent_id": str(uuid.uuid4()),
                "diff": "",
                "description": "x",
            },
        )
        assert resp.status_code == 404

    def test_get_feedback(self, client: TestClient, runtime: CLIRuntime) -> None:
        goal = runtime.create_goal(
            title="Refactor", description="Refactor the auth module"
        )
        breakdown = runtime.goal_manager.activate(goal.goal_id)
        task = breakdown.tasks[0]

        # Claim + submit to generate feedback
        client.post(f"/api/agents/sdk/tasks/{task.task_id}/claim")
        client.post(
            f"/api/agents/sdk/tasks/{task.task_id}/submit",
            json={
                "task_id": str(task.task_id),
                "agent_id": "test-agent",
                "intent_id": str(uuid.uuid4()),
                "diff": "",
                "description": "Refactored auth",
                "files_changed": ["src/auth.py"],
            },
        )

        resp = client.get(f"/api/agents/sdk/tasks/{task.task_id}/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == str(task.task_id)
        assert "status" in data

    def test_get_feedback_not_found(self, client: TestClient) -> None:
        fake_id = uuid.uuid4()
        resp = client.get(f"/api/agents/sdk/tasks/{fake_id}/feedback")
        assert resp.status_code == 404

    def test_register_creates_trust_profile(
        self, client: TestClient, runtime: CLIRuntime
    ) -> None:
        client.post(
            "/api/agents/sdk/register",
            json={
                "agent_id": "new-agent",
                "name": "New Agent",
                "capabilities": ["python"],
            },
        )
        profile = runtime.trust_tracker.get_profile("new-agent")
        assert profile.agent_id == "new-agent"


# ---------------------------------------------------------------------------
# End-to-end flow
# ---------------------------------------------------------------------------


class TestEndToEndFlow:
    """Full lifecycle: register -> get tasks -> claim -> submit -> feedback."""

    def test_full_agent_lifecycle(
        self, client: TestClient, runtime: CLIRuntime
    ) -> None:
        # 1. Register
        resp = client.post(
            "/api/agents/sdk/register",
            json={
                "agent_id": "e2e-agent",
                "name": "E2E Agent",
                "capabilities": ["python", "api"],
                "languages": ["python"],
            },
        )
        assert resp.status_code == 200

        # 2. Human creates and activates a goal
        goal = runtime.create_goal(
            title="Add caching",
            description="Add Redis caching layer",
            constraints=["Must not break existing APIs"],
            acceptance_criteria=["Cache hit rate > 80%"],
        )
        runtime.goal_manager.activate(goal.goal_id)

        # 3. Agent lists available tasks
        resp = client.get("/api/agents/sdk/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert len(tasks) > 0
        task = tasks[0]
        task_id = task["task_id"]

        # 4. Agent claims a task
        resp = client.post(f"/api/agents/sdk/tasks/{task_id}/claim")
        assert resp.status_code == 200
        claimed = resp.json()
        assert claimed["task_id"] == task_id

        # 5. Agent submits work
        intent_id = uuid.uuid4()
        resp = client.post(
            f"/api/agents/sdk/tasks/{task_id}/submit",
            json={
                "task_id": task_id,
                "agent_id": "e2e-agent",
                "intent_id": str(intent_id),
                "diff": "--- a/cache.py\n+++ b/cache.py\n@@ -0,0 +1 @@\n+# cache",
                "description": "Added Redis caching layer",
                "test_command": "pytest tests/test_cache.py",
                "files_changed": ["cache.py"],
            },
        )
        assert resp.status_code == 200
        feedback = resp.json()
        assert feedback["task_id"] == task_id
        assert feedback["status"] in ("accepted", "rejected", "needs_revision")
        assert "message" in feedback

        # 6. Agent retrieves feedback later
        resp = client.get(f"/api/agents/sdk/tasks/{task_id}/feedback")
        assert resp.status_code == 200
        stored_feedback = resp.json()
        assert stored_feedback["task_id"] == task_id
        assert stored_feedback["status"] == feedback["status"]
