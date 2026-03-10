"""Tests for the Projects API endpoints."""

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
# Create project
# ---------------------------------------------------------------------------


def test_create_project(client: TestClient):
    resp = client.post("/api/projects", json={
        "title": "Auth Revamp",
        "description": "Redesign the authentication module",
        "priority": "high",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Auth Revamp"
    assert data["status"] == "draft"
    assert data["priority"] == "high"
    assert "project_id" in data


def test_create_project_with_all_fields(client: TestClient):
    resp = client.post("/api/projects", json={
        "title": "Full Project",
        "description": "A project with all fields",
        "constraints": ["no-breaking-changes"],
        "priority": "urgent",
        "target_services": ["auth", "api"],
        "tags": ["security", "backend"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["constraints"] == ["no-breaking-changes"]
    assert data["target_services"] == ["auth", "api"]
    assert data["tags"] == ["security", "backend"]


def test_create_project_defaults(client: TestClient):
    resp = client.post("/api/projects", json={
        "title": "Defaults",
        "description": "Just title and description",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["priority"] == "medium"
    assert data["constraints"] == []
    assert data["tags"] == []


def test_create_project_invalid_priority(client: TestClient):
    resp = client.post("/api/projects", json={
        "title": "Bad",
        "description": "Invalid priority",
        "priority": "nonsense",
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List projects
# ---------------------------------------------------------------------------


def test_list_projects_empty(client: TestClient):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_projects(client: TestClient):
    client.post("/api/projects", json={
        "title": "Project A",
        "description": "First project",
    })
    client.post("/api/projects", json={
        "title": "Project B",
        "description": "Second project",
    })

    resp = client.get("/api/projects")
    assert resp.status_code == 200
    projects = resp.json()
    assert len(projects) == 2


def test_list_projects_filter_by_status(client: TestClient):
    # Create a project — it starts as draft
    client.post("/api/projects", json={
        "title": "Filterable",
        "description": "For status filtering",
    })

    resp = client.get("/api/projects", params={"status": "draft"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = client.get("/api/projects", params={"status": "active"})
    assert resp.status_code == 200
    assert len(resp.json()) == 0


# ---------------------------------------------------------------------------
# Get project
# ---------------------------------------------------------------------------


def test_get_project(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "Fetchable",
        "description": "Get by ID",
    })
    project_id = create_resp.json()["project_id"]

    resp = client.get(f"/api/projects/{project_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == project_id
    assert data["title"] == "Fetchable"


def test_get_project_not_found(client: TestClient):
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/projects/{fake_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Activate project
# ---------------------------------------------------------------------------


def test_activate_project(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "Activatable",
        "description": "Will be activated",
    })
    project_id = create_resp.json()["project_id"]

    resp = client.post(f"/api/projects/{project_id}/activate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert len(data["milestones"]) == 3  # planner generates 3


def test_activate_project_not_found(client: TestClient):
    fake_id = str(uuid.uuid4())
    resp = client.post(f"/api/projects/{fake_id}/activate")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cancel project
# ---------------------------------------------------------------------------


def test_cancel_project(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "Cancel me",
        "description": "Will be cancelled",
    })
    project_id = create_resp.json()["project_id"]

    resp = client.post(f"/api/projects/{project_id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"


def test_cancel_already_cancelled(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "Cancel twice",
        "description": "Double cancel",
    })
    project_id = create_resp.json()["project_id"]

    client.post(f"/api/projects/{project_id}/cancel")
    resp = client.post(f"/api/projects/{project_id}/cancel")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------


def test_list_milestones_empty(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "No milestones",
        "description": "Draft project",
    })
    project_id = create_resp.json()["project_id"]

    resp = client.get(f"/api/projects/{project_id}/milestones")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_milestones_after_activate(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "With milestones",
        "description": "Activated project",
    })
    project_id = create_resp.json()["project_id"]

    client.post(f"/api/projects/{project_id}/activate")

    resp = client.get(f"/api/projects/{project_id}/milestones")
    assert resp.status_code == 200
    milestones = resp.json()
    assert len(milestones) == 3
    # First milestone should be active
    assert milestones[0]["status"] == "active"
    assert milestones[1]["status"] == "pending"


def test_complete_milestone(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "Completable",
        "description": "Has milestones to complete",
    })
    project_id = create_resp.json()["project_id"]

    client.post(f"/api/projects/{project_id}/activate")

    ms_resp = client.get(f"/api/projects/{project_id}/milestones")
    first_ms_id = ms_resp.json()[0]["milestone_id"]

    resp = client.post(
        f"/api/projects/{project_id}/milestones/{first_ms_id}/complete"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"


def test_complete_milestone_not_found(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "Bad milestone",
        "description": "Milestone does not exist",
    })
    project_id = create_resp.json()["project_id"]
    fake_ms_id = str(uuid.uuid4())

    resp = client.post(
        f"/api/projects/{project_id}/milestones/{fake_ms_id}/complete"
    )
    assert resp.status_code == 404


def test_complete_pending_milestone_fails(client: TestClient):
    """Cannot complete a milestone that is not active."""
    create_resp = client.post("/api/projects", json={
        "title": "Pending milestone",
        "description": "Has pending milestones",
    })
    project_id = create_resp.json()["project_id"]

    client.post(f"/api/projects/{project_id}/activate")

    ms_resp = client.get(f"/api/projects/{project_id}/milestones")
    # Second milestone is pending
    second_ms_id = ms_resp.json()[1]["milestone_id"]

    resp = client.post(
        f"/api/projects/{project_id}/milestones/{second_ms_id}/complete"
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Project goals
# ---------------------------------------------------------------------------


def test_list_project_goals_empty(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "No goals",
        "description": "Draft project",
    })
    project_id = create_resp.json()["project_id"]

    resp = client.get(f"/api/projects/{project_id}/goals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_project_goals_after_activate(client: TestClient):
    create_resp = client.post("/api/projects", json={
        "title": "With goals",
        "description": "Activation creates goals",
    })
    project_id = create_resp.json()["project_id"]

    client.post(f"/api/projects/{project_id}/activate")

    resp = client.get(f"/api/projects/{project_id}/goals")
    assert resp.status_code == 200
    goals = resp.json()
    # Activation creates a goal for the first active milestone
    assert len(goals) >= 1
    assert "goal_id" in goals[0]


def test_project_goals_not_found(client: TestClient):
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/projects/{fake_id}/goals")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


def test_full_project_lifecycle(client: TestClient):
    """Create -> activate -> complete milestones -> project completes."""
    create_resp = client.post("/api/projects", json={
        "title": "Lifecycle test",
        "description": "Full lifecycle",
    })
    project_id = create_resp.json()["project_id"]

    # Activate
    activate_resp = client.post(f"/api/projects/{project_id}/activate")
    assert activate_resp.json()["status"] == "active"

    # Complete all milestones
    for _ in range(3):
        ms_resp = client.get(f"/api/projects/{project_id}/milestones")
        active_ms = [
            m for m in ms_resp.json() if m["status"] == "active"
        ]
        if not active_ms:
            break
        ms_id = active_ms[0]["milestone_id"]
        client.post(
            f"/api/projects/{project_id}/milestones/{ms_id}/complete"
        )

    # Project should be completed
    proj_resp = client.get(f"/api/projects/{project_id}")
    assert proj_resp.json()["status"] == "completed"
