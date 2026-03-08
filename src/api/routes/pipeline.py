"""Pipeline run endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from src.cli.runtime import CLIRuntime

from ..dependencies import get_runtime
from ..schemas import RunApproveRequest, RunRejectRequest

router = APIRouter(prefix="/api/runs", tags=["pipeline"])


@router.get("")
def list_runs(
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """List recent pipeline runs."""
    try:
        runs = runtime.list_runs(agent_id=agent_id, status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [r.model_dump(mode="json") for r in runs]


@router.post("/{run_id}/approve")
def approve_run(
    run_id: str,
    body: RunApproveRequest,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Approve a blocked pipeline run."""
    try:
        run = runtime.approve_run(run_id, comment=body.comment)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return run.model_dump(mode="json")


@router.post("/{run_id}/reject")
def reject_run(
    run_id: str,
    body: RunRejectRequest,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Reject a blocked pipeline run."""
    try:
        run = runtime.reject_run(run_id, reason=body.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return run.model_dump(mode="json")
