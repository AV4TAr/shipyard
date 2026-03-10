"""Routing API endpoints — agent registration, task routing, decisions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.cli.runtime import CLIRuntime
from src.routing.models import AgentStatus

from ..dependencies import get_runtime

router = APIRouter(prefix="/api/routing", tags=["routing"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RegisterAgentRequest(BaseModel):
    """Payload for registering a new agent via the routing API."""

    agent_id: str
    name: str
    capabilities: list[str]
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = 1


class UpdateStatusRequest(BaseModel):
    """Payload for updating an agent's operational status."""

    status: str  # one of AgentStatus values


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/agents")
def list_routing_agents(runtime: CLIRuntime = Depends(get_runtime)) -> list[dict[str, Any]]:
    """List all agents registered in the routing system."""
    agents = runtime.list_registered_agents()
    results: list[dict[str, Any]] = []
    for a in agents:
        agent_dict = a.model_dump(mode="json")
        # Attach trust score for convenience.
        agent_dict["trust_score"] = runtime.trust_tracker.compute_trust_score(
            a.agent_id
        )
        results.append(agent_dict)
    return results


@router.post("/agents")
def register_routing_agent(
    body: RegisterAgentRequest,
    runtime: CLIRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """Register a new agent with the routing system."""
    try:
        reg = runtime.register_agent(
            agent_id=body.agent_id,
            name=body.name,
            capabilities=body.capabilities,
            languages=body.languages,
            frameworks=body.frameworks,
            max_concurrent_tasks=body.max_concurrent_tasks,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return reg.model_dump(mode="json")


@router.get("/agents/{agent_id}")
def get_routing_agent(
    agent_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """Get details of a single routing agent."""
    if runtime.agent_registry is None:
        raise HTTPException(status_code=500, detail="Routing not initialised")
    try:
        agent = runtime.agent_registry.get(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    result = agent.model_dump(mode="json")
    result["trust_score"] = runtime.trust_tracker.compute_trust_score(agent_id)
    profile = runtime.trust_tracker.get_profile(agent_id)
    result["domain_scores"] = profile.domain_scores
    return result


@router.put("/agents/{agent_id}/status")
def update_agent_status(
    agent_id: str,
    body: UpdateStatusRequest,
    runtime: CLIRuntime = Depends(get_runtime),
) -> dict[str, str]:
    """Update an agent's operational status."""
    if runtime.agent_registry is None:
        raise HTTPException(status_code=500, detail="Routing not initialised")
    try:
        status_enum = AgentStatus(body.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {body.status!r}. "
            f"Valid: {[s.value for s in AgentStatus]}",
        )
    try:
        runtime.agent_registry.update_status(agent_id, status_enum)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"agent_id": agent_id, "status": status_enum.value}


@router.delete("/agents/{agent_id}")
def unregister_agent(
    agent_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
) -> dict[str, str]:
    """Unregister an agent from the routing system."""
    if runtime.agent_registry is None:
        raise HTTPException(status_code=500, detail="Routing not initialised")
    try:
        runtime.agent_registry.unregister(agent_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"detail": f"Agent {agent_id!r} unregistered"}


@router.post("/route/{task_id}")
def route_task(
    task_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """Route a specific task to the best available agent."""
    try:
        decision = runtime.route_task(task_id)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return decision.model_dump(mode="json")


@router.post("/route-goal/{goal_id}")
def route_goal(
    goal_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
) -> list[dict[str, Any]]:
    """Auto-route all ready tasks for a goal."""
    try:
        decisions = runtime.auto_route_goal(goal_id)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return [d.model_dump(mode="json") for d in decisions]


@router.get("/decisions")
def list_decisions(
    runtime: CLIRuntime = Depends(get_runtime),
) -> list[dict[str, Any]]:
    """List recent routing decisions."""
    if runtime.routing_bridge is None:
        return []
    return [d.model_dump(mode="json") for d in runtime.routing_bridge.decisions]
