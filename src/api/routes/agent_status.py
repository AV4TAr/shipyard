"""Agent status, active tasks, pipeline freeze, and agent ban endpoints."""

from __future__ import annotations

import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.cli.runtime import CLIRuntime
from src.goals.models import TaskStatus

from ..dependencies import get_runtime

router = APIRouter(prefix="/api", tags=["agents"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class BanRequest(BaseModel):
    reason: Optional[str] = ""


# ---------------------------------------------------------------------------
# Agent status endpoints (prefix: /api/agents)
# ---------------------------------------------------------------------------


@router.get("/agents/status")
def get_agent_statuses(runtime: CLIRuntime = Depends(get_runtime)):
    """Return real-time status for all known agents.

    Each entry includes the agent's current phase, active task, and
    elapsed time — used by the Command Center for live agent cards.
    """
    if runtime.lease_manager is None:
        return []

    statuses = runtime.lease_manager.get_all_agent_statuses()
    return [
        {
            "agent_id": s.agent_id,
            "phase": s.phase.value,
            "current_task_id": str(s.current_task_id) if s.current_task_id else None,
            "current_task_title": s.current_task_title,
            "last_heartbeat": s.last_heartbeat.isoformat(),
            "elapsed_seconds": round(s.elapsed_seconds, 1),
            "started_at": s.started_at.isoformat() if s.started_at else None,
        }
        for s in statuses
    ]


@router.get("/agents/leases")
def get_active_leases(runtime: CLIRuntime = Depends(get_runtime)):
    """Return all active (non-expired) task leases."""
    if runtime.lease_manager is None:
        return []

    leases = runtime.lease_manager.get_active_leases()
    return [
        {
            "task_id": str(lease.task_id),
            "agent_id": lease.agent_id,
            "lease_expires_at": lease.lease_expires_at.isoformat(),
            "lease_duration_seconds": lease.lease_duration_seconds,
        }
        for lease in leases
    ]


@router.get("/agents/tasks/active")
def get_active_tasks(runtime: CLIRuntime = Depends(get_runtime)):
    """Return all tasks currently being worked on (ASSIGNED or IN_PROGRESS).

    Includes lease and worktree info for the dashboard.
    """
    active_tasks = []
    for goal in runtime.goal_manager.list_goals():
        for task in runtime.goal_manager.get_tasks(goal.goal_id):
            if task.status in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                lease = None
                if runtime.lease_manager:
                    lease_info = runtime.lease_manager.get_lease(task.task_id)
                    if lease_info:
                        lease = {
                            "agent_id": lease_info.agent_id,
                            "expires_at": lease_info.lease_expires_at.isoformat(),
                        }
                active_tasks.append({
                    "task_id": str(task.task_id),
                    "goal_id": str(task.goal_id),
                    "title": task.title,
                    "status": task.status.value,
                    "claimed_by": task.claimed_by,
                    "worktree_path": task.worktree_path,
                    "branch_name": task.branch_name,
                    "lease": lease,
                })
    return active_tasks


# ---------------------------------------------------------------------------
# Pipeline freeze (kill switch) endpoints (prefix: /api/pipeline)
# ---------------------------------------------------------------------------


@router.get("/pipeline/freeze")
def get_freeze_state(runtime: CLIRuntime = Depends(get_runtime)):
    """Return whether the pipeline is currently frozen."""
    if runtime.lease_manager is None:
        return {"frozen": False}
    return {"frozen": runtime.lease_manager.frozen}


@router.post("/pipeline/freeze")
def freeze_pipeline(runtime: CLIRuntime = Depends(get_runtime)):
    """Freeze the pipeline — block all new claims and submissions."""
    if runtime.lease_manager is None:
        raise HTTPException(status_code=501, detail="Lease management not enabled")
    runtime.lease_manager.freeze()
    return {"frozen": True}


@router.post("/pipeline/unfreeze")
def unfreeze_pipeline(runtime: CLIRuntime = Depends(get_runtime)):
    """Unfreeze the pipeline — allow claims and submissions again."""
    if runtime.lease_manager is None:
        raise HTTPException(status_code=501, detail="Lease management not enabled")
    runtime.lease_manager.unfreeze()
    return {"frozen": False}


# ---------------------------------------------------------------------------
# Agent ban endpoints (prefix: /api/agents)
# ---------------------------------------------------------------------------


@router.get("/agents/banned")
def list_banned_agents(runtime: CLIRuntime = Depends(get_runtime)):
    """Return all currently banned agents."""
    if runtime.lease_manager is None:
        return []
    banned = runtime.lease_manager.get_banned_agents()
    return [
        {"agent_id": agent_id, "reason": reason}
        for agent_id, reason in banned.items()
    ]


@router.post("/agents/{agent_id}/ban")
def ban_agent(
    agent_id: str,
    body: BanRequest,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Ban an agent from claiming tasks."""
    if runtime.lease_manager is None:
        raise HTTPException(status_code=501, detail="Lease management not enabled")
    runtime.lease_manager.ban_agent(agent_id, body.reason or "")
    return {"agent_id": agent_id, "banned": True, "reason": body.reason or ""}


@router.delete("/agents/{agent_id}/ban")
def unban_agent(
    agent_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Unban an agent — allow it to claim tasks again."""
    if runtime.lease_manager is None:
        raise HTTPException(status_code=501, detail="Lease management not enabled")
    runtime.lease_manager.unban_agent(agent_id)
    return {"agent_id": agent_id, "banned": False}


# ---------------------------------------------------------------------------
# Lease revocation endpoint (prefix: /api/tasks)
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/revoke")
def revoke_lease(
    task_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Force-revoke a lease on a task, resetting it to PENDING."""
    if runtime.lease_manager is None:
        raise HTTPException(status_code=501, detail="Lease management not enabled")
    try:
        tid = _uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid task ID")
    revoked = runtime.lease_manager.revoke(tid)
    if not revoked:
        raise HTTPException(status_code=404, detail="No active lease for task {}".format(task_id))
    return {"task_id": task_id, "revoked": True}


@router.post("/tasks/{task_id}/reset")
def reset_task(
    task_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Reset a failed/assigned task back to PENDING so agents can retry."""
    try:
        tid = _uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid task ID")
    try:
        task = runtime.goal_manager.update_task_status(tid, TaskStatus.PENDING)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task {} not found".format(task_id))
    # Also revoke any lease
    if runtime.lease_manager is not None:
        runtime.lease_manager.revoke(tid)
    return {"task_id": task_id, "status": "pending"}
