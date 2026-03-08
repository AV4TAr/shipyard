"""Tests for the FastAPI backend."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.dependencies import get_runtime
from src.cli.runtime import CLIRuntime


@pytest.fixture()
def runtime() -> CLIRuntime:
    """Fresh CLIRuntime for each test."""
    return CLIRuntime.from_defaults()


@pytest.fixture()
def client(runtime: CLIRuntime) -> TestClient:
    """FastAPI TestClient wired to a fresh runtime."""
    app = create_app()
    app.dependency_overrides[get_runtime] = lambda: runtime
    return TestClient(app)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_returns_200_with_expected_keys(client: TestClient):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    expected_keys = {
        "active_goals",
        "pipeline_runs_in_progress",
        "pending_approvals",
        "agent_count",
        "active_agents",
        "deploy_queue_length",
    }
    assert expected_keys <= set(data.keys())


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


def test_create_goal(client: TestClient):
    resp = client.post("/api/goals", json={
        "title": "Add rate limiting",
        "description": "Implement rate limiting on all API endpoints",
        "priority": "high",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Add rate limiting"
    assert data["status"] == "draft"
    assert data["priority"] == "high"
    assert "goal_id" in data


def test_list_goals(client: TestClient):
    # Create two goals
    client.post("/api/goals", json={
        "title": "Goal A",
        "description": "First goal",
    })
    client.post("/api/goals", json={
        "title": "Goal B",
        "description": "Second goal",
    })

    resp = client.get("/api/goals")
    assert resp.status_code == 200
    goals = resp.json()
    assert len(goals) == 2


def test_get_goal_with_tasks(client: TestClient):
    # Create a goal
    create_resp = client.post("/api/goals", json={
        "title": "Test goal",
        "description": "A goal to test",
    })
    goal_id = create_resp.json()["goal_id"]

    resp = client.get(f"/api/goals/{goal_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert "tasks" in data
    assert data["goal"]["goal_id"] == goal_id


def test_activate_goal_returns_task_breakdown(client: TestClient):
    create_resp = client.post("/api/goals", json={
        "title": "Activate me",
        "description": "Goal to activate",
    })
    goal_id = create_resp.json()["goal_id"]

    resp = client.post(f"/api/goals/{goal_id}/activate")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert len(data["tasks"]) > 0


def test_cancel_goal(client: TestClient):
    create_resp = client.post("/api/goals", json={
        "title": "Cancel me",
        "description": "Goal to cancel",
    })
    goal_id = create_resp.json()["goal_id"]

    resp = client.post(f"/api/goals/{goal_id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"


def test_get_goal_not_found(client: TestClient):
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/goals/{fake_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Pipeline runs
# ---------------------------------------------------------------------------


def test_list_runs_empty(client: TestClient):
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_approve_invalid_run_returns_404(client: TestClient):
    fake_id = str(uuid.uuid4())
    resp = client.post(f"/api/runs/{fake_id}/approve", json={})
    assert resp.status_code == 404


def test_reject_invalid_run_returns_404(client: TestClient):
    fake_id = str(uuid.uuid4())
    resp = client.post(f"/api/runs/{fake_id}/reject", json={"reason": "bad code"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def test_list_agents_empty(client: TestClient):
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


def test_list_queue_empty(client: TestClient):
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def test_list_constraints(client: TestClient):
    resp = client.get("/api/constraints")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    # Each constraint should have key fields
    first = data[0]
    assert "constraint_id" in first
    assert "category" in first
    assert "severity" in first


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_create_goal_invalid_priority(client: TestClient):
    resp = client.post("/api/goals", json={
        "title": "Bad priority",
        "description": "Should fail",
        "priority": "nonsense",
    })
    assert resp.status_code == 422


def test_goal_list_filter_by_status(client: TestClient):
    # Create and activate a goal
    create_resp = client.post("/api/goals", json={
        "title": "Filter test",
        "description": "For filtering",
    })
    goal_id = create_resp.json()["goal_id"]
    client.post(f"/api/goals/{goal_id}/activate")

    # Filter by active status
    resp = client.get("/api/goals", params={"status": "active"})
    assert resp.status_code == 200
    goals = resp.json()
    assert all(g["status"] == "active" for g in goals)

    # Filter by draft — should be empty now
    resp = client.get("/api/goals", params={"status": "draft"})
    assert resp.status_code == 200
    assert len(resp.json()) == 0
