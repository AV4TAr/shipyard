"""Agent profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.cli.runtime import CLIRuntime

from ..dependencies import get_runtime

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents(runtime: CLIRuntime = Depends(get_runtime)):
    """List all agent profiles."""
    agents = runtime.list_agents()
    return [a.model_dump(mode="json") for a in agents]


@router.get("/{agent_id}")
def get_agent(agent_id: str, runtime: CLIRuntime = Depends(get_runtime)):
    """Get a single agent profile."""
    try:
        profile = runtime.get_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return profile.model_dump(mode="json")
