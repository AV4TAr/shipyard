"""Tests for pipeline freeze, project pause/cancel, agent bans, and lease revocation."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src.cli.runtime import CLIRuntime
from src.goals.models import TaskStatus
from src.leases.manager import AgentBannedError, PipelineFrozenError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime() -> CLIRuntime:
    """Create a CLIRuntime with in-memory storage for testing."""
    return CLIRuntime.from_defaults(storage_backend="memory")


def _make_client(runtime: CLIRuntime) -> TestClient:
    """Create a FastAPI TestClient wired to the given runtime."""
    from fastapi import FastAPI

    from src.api.dependencies import get_runtime
    from src.api.routes import agent_status, projects
    from src.sdk.routes import mount_sdk_routes

    app = FastAPI()
    app.dependency_overrides[get_runtime] = lambda: runtime
    app.include_router(mount_sdk_routes(runtime))
    app.include_router(agent_status.router)
    app.include_router(projects.router)
    return TestClient(app)


def _create_goal_with_task(runtime: CLIRuntime) -> tuple:
    """Create a goal, activate it, and return (goal, task)."""
    goal = runtime.create_goal(
        title="Test goal",
        description="For kill switch tests",
    )
    runtime.activate_goal(str(goal.goal_id))
    tasks = runtime.goal_manager.get_tasks(goal.goal_id)
    assert len(tasks) > 0
    return goal, tasks[0]


def _create_project_with_task(runtime: CLIRuntime) -> tuple:
    """Create a project with a milestone and return (project, goal, task)."""
    project = runtime.create_project(
        title="Test Project",
        description="For kill switch tests",
    )
    runtime.project_manager.add_milestone(
        project.project_id,
        title="Milestone 1",
        description="First milestone",
    )
    runtime.project_manager.plan(project.project_id)
    project = runtime.project_manager.activate(project.project_id)

    # Get the goal that was auto-created for the milestone
    milestones = project.milestones
    assert len(milestones) > 0
    assert len(milestones[0].goal_ids) > 0
    goal_id = milestones[0].goal_ids[0]
    goal = runtime.goal_manager.get(goal_id)
    tasks = runtime.goal_manager.get_tasks(goal_id)
    assert len(tasks) > 0
    return project, goal, tasks[0]


# ---------------------------------------------------------------------------
# Unit tests: LeaseManager freeze
# ---------------------------------------------------------------------------


class TestPipelineFreeze:
    """Test the global pipeline freeze (kill switch)."""

    def test_freeze_blocks_claims(self):
        """When frozen, claim() raises PipelineFrozenError."""
        rt = _make_runtime()
        _, task = _create_goal_with_task(rt)
        rt.lease_manager.freeze()
        with pytest.raises(PipelineFrozenError, match="frozen"):
            rt.lease_manager.claim(task.task_id, "agent-test")

    def test_unfreeze_allows_claims(self):
        """After unfreezing, claims work again."""
        rt = _make_runtime()
        _, task = _create_goal_with_task(rt)
        rt.lease_manager.freeze()
        rt.lease_manager.unfreeze()
        lease = rt.lease_manager.claim(task.task_id, "agent-test")
        assert lease.agent_id == "agent-test"

    def test_freeze_state_persists(self):
        """Freeze state is readable and persists across checks."""
        rt = _make_runtime()
        assert rt.lease_manager.frozen is False
        rt.lease_manager.freeze()
        assert rt.lease_manager.frozen is True
        rt.lease_manager.unfreeze()
        assert rt.lease_manager.frozen is False


class TestPipelineFreezeHTTP:
    """Test freeze/unfreeze via HTTP endpoints."""

    def test_freeze_blocks_claim_http(self):
        """SDK claim returns 503 when pipeline is frozen."""
        rt = _make_runtime()
        client = _make_client(rt)
        _, task = _create_goal_with_task(rt)

        # Freeze
        resp = client.post("/api/pipeline/freeze")
        assert resp.status_code == 200
        assert resp.json()["frozen"] is True

        # Try to claim
        resp = client.post(
            "/api/agents/sdk/tasks/{}/claim".format(task.task_id),
            params={"agent_id": "agent-test"},
        )
        assert resp.status_code == 503
        assert "frozen" in resp.json()["detail"].lower()

    def test_freeze_blocks_submission_http(self):
        """SDK submit returns 503 when pipeline is frozen."""
        rt = _make_runtime()
        client = _make_client(rt)
        _, task = _create_goal_with_task(rt)

        # Claim first (before freeze)
        resp = client.post(
            "/api/agents/sdk/tasks/{}/claim".format(task.task_id),
            params={"agent_id": "agent-test"},
        )
        assert resp.status_code == 200

        # Freeze
        client.post("/api/pipeline/freeze")

        # Try to submit
        resp = client.post(
            "/api/agents/sdk/tasks/{}/submit".format(task.task_id),
            json={
                "task_id": str(task.task_id),
                "agent_id": "agent-test",
                "intent_id": str(uuid.uuid4()),
                "description": "test",
                "files_changed": [],
            },
        )
        assert resp.status_code == 503
        assert "frozen" in resp.json()["detail"].lower()

    def test_unfreeze_allows_claims_http(self):
        """After unfreezing, claims succeed again."""
        rt = _make_runtime()
        client = _make_client(rt)
        _, task = _create_goal_with_task(rt)

        client.post("/api/pipeline/freeze")
        client.post("/api/pipeline/unfreeze")

        resp = client.post(
            "/api/agents/sdk/tasks/{}/claim".format(task.task_id),
            params={"agent_id": "agent-test"},
        )
        assert resp.status_code == 200

    def test_get_freeze_state(self):
        """GET /api/pipeline/freeze returns current state."""
        rt = _make_runtime()
        client = _make_client(rt)

        resp = client.get("/api/pipeline/freeze")
        assert resp.json() == {"frozen": False}

        client.post("/api/pipeline/freeze")
        resp = client.get("/api/pipeline/freeze")
        assert resp.json() == {"frozen": True}

    def test_heartbeat_returns_cancel_when_frozen(self):
        """Heartbeat returns cancel=true when pipeline is frozen."""
        rt = _make_runtime()
        client = _make_client(rt)
        _, task = _create_goal_with_task(rt)

        # Claim
        client.post(
            "/api/agents/sdk/tasks/{}/claim".format(task.task_id),
            params={"agent_id": "agent-test"},
        )

        # Freeze
        client.post("/api/pipeline/freeze")

        # Heartbeat
        resp = client.post(
            "/api/agents/sdk/tasks/{}/heartbeat".format(task.task_id),
            json={"agent_id": "agent-test"},
        )
        assert resp.status_code == 200
        assert resp.json()["cancel"] is True


# ---------------------------------------------------------------------------
# Project pause/resume
# ---------------------------------------------------------------------------


class TestProjectPause:
    """Test project pause/resume and their effect on task visibility."""

    def test_pause_hides_tasks(self):
        """Tasks from paused projects don't appear in task listing."""
        rt = _make_runtime()
        client = _make_client(rt)
        project, goal, task = _create_project_with_task(rt)

        # Tasks should be visible
        resp = client.get("/api/agents/sdk/tasks")
        task_ids = [t["task_id"] for t in resp.json()]
        assert str(task.task_id) in task_ids

        # Pause the project
        resp = client.post("/api/projects/{}/pause".format(project.project_id))
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

        # Tasks should be hidden
        resp = client.get("/api/agents/sdk/tasks")
        task_ids = [t["task_id"] for t in resp.json()]
        assert str(task.task_id) not in task_ids

    def test_pause_blocks_claims(self):
        """Claims on tasks from paused projects are rejected."""
        rt = _make_runtime()
        client = _make_client(rt)
        project, goal, task = _create_project_with_task(rt)

        # Pause
        client.post("/api/projects/{}/pause".format(project.project_id))

        # Try to claim
        resp = client.post(
            "/api/agents/sdk/tasks/{}/claim".format(task.task_id),
            params={"agent_id": "agent-test"},
        )
        assert resp.status_code == 409
        assert "paused" in resp.json()["detail"].lower()

    def test_resume_restores_tasks(self):
        """After resuming, tasks are visible again."""
        rt = _make_runtime()
        client = _make_client(rt)
        project, goal, task = _create_project_with_task(rt)

        # Pause then resume
        client.post("/api/projects/{}/pause".format(project.project_id))
        resp = client.post("/api/projects/{}/resume".format(project.project_id))
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

        # Tasks visible again
        resp = client.get("/api/agents/sdk/tasks")
        task_ids = [t["task_id"] for t in resp.json()]
        assert str(task.task_id) in task_ids


# ---------------------------------------------------------------------------
# Agent ban
# ---------------------------------------------------------------------------


class TestAgentBan:
    """Test agent banning and unbanning."""

    def test_ban_blocks_claims(self):
        """Banned agents cannot claim tasks."""
        rt = _make_runtime()
        client = _make_client(rt)
        _, task = _create_goal_with_task(rt)

        # Ban the agent
        resp = client.post(
            "/api/agents/agent-test/ban",
            json={"reason": "testing bans"},
        )
        assert resp.status_code == 200
        assert resp.json()["banned"] is True

        # Try to claim
        resp = client.post(
            "/api/agents/sdk/tasks/{}/claim".format(task.task_id),
            params={"agent_id": "agent-test"},
        )
        assert resp.status_code == 403
        assert "banned" in resp.json()["detail"].lower()

    def test_unban_allows_claims(self):
        """Unbanned agents can claim tasks again."""
        rt = _make_runtime()
        client = _make_client(rt)
        _, task = _create_goal_with_task(rt)

        # Ban then unban
        client.post("/api/agents/agent-test/ban", json={"reason": "test"})
        resp = client.delete("/api/agents/agent-test/ban")
        assert resp.status_code == 200
        assert resp.json()["banned"] is False

        # Claim succeeds
        resp = client.post(
            "/api/agents/sdk/tasks/{}/claim".format(task.task_id),
            params={"agent_id": "agent-test"},
        )
        assert resp.status_code == 200

    def test_banned_agents_list(self):
        """GET /api/agents/banned returns all banned agents."""
        rt = _make_runtime()
        client = _make_client(rt)

        resp = client.get("/api/agents/banned")
        assert resp.json() == []

        client.post("/api/agents/agent-alpha/ban", json={"reason": "bad agent"})
        client.post("/api/agents/agent-beta/ban", json={"reason": "also bad"})

        resp = client.get("/api/agents/banned")
        banned = resp.json()
        assert len(banned) == 2
        agent_ids = {b["agent_id"] for b in banned}
        assert "agent-alpha" in agent_ids
        assert "agent-beta" in agent_ids

    def test_ban_at_lease_manager_level(self):
        """LeaseManager.claim raises AgentBannedError for banned agents."""
        rt = _make_runtime()
        _, task = _create_goal_with_task(rt)
        rt.lease_manager.ban_agent("agent-bad", "testing")
        with pytest.raises(AgentBannedError, match="banned"):
            rt.lease_manager.claim(task.task_id, "agent-bad")


# ---------------------------------------------------------------------------
# Lease revocation
# ---------------------------------------------------------------------------


class TestLeaseRevocation:
    """Test force-revoking leases."""

    def test_revoke_resets_task(self):
        """Revoking a lease resets the task to PENDING."""
        rt = _make_runtime()
        _, task = _create_goal_with_task(rt)

        # Claim then revoke
        rt.goal_manager.update_task_status(task.task_id, TaskStatus.ASSIGNED)
        rt.lease_manager.claim(task.task_id, "agent-test")
        revoked = rt.lease_manager.revoke(task.task_id)
        assert revoked is True

        # Lease gone
        assert rt.lease_manager.get_lease(task.task_id) is None
        # Task reset to PENDING
        tasks = rt.goal_manager.get_tasks(task.goal_id)
        found = [t for t in tasks if t.task_id == task.task_id]
        assert found[0].status == TaskStatus.PENDING

    def test_revoke_nonexistent_returns_false(self):
        """Revoking a nonexistent lease returns False (no error)."""
        rt = _make_runtime()
        result = rt.lease_manager.revoke(uuid.uuid4())
        assert result is False

    def test_revoke_via_http(self):
        """POST /api/tasks/{id}/revoke force-revokes a lease."""
        rt = _make_runtime()
        client = _make_client(rt)
        _, task = _create_goal_with_task(rt)

        # Claim
        client.post(
            "/api/agents/sdk/tasks/{}/claim".format(task.task_id),
            params={"agent_id": "agent-test"},
        )

        # Revoke
        resp = client.post("/api/tasks/{}/revoke".format(task.task_id))
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

    def test_revoke_nonexistent_http(self):
        """POST /api/tasks/{id}/revoke returns 404 for no lease."""
        rt = _make_runtime()
        client = _make_client(rt)
        fake_id = str(uuid.uuid4())
        resp = client.post("/api/tasks/{}/revoke".format(fake_id))
        assert resp.status_code == 404
