"""Goal CRUD endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from src.cli.runtime import CLIRuntime

from ..dependencies import get_runtime
from ..schemas import GoalCreateRequest

router = APIRouter(prefix="/api/goals", tags=["goals"])


@router.post("")
def create_goal(
    body: GoalCreateRequest,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Create a new goal."""
    try:
        goal = runtime.create_goal(
            title=body.title,
            description=body.description,
            constraints=body.constraints,
            acceptance_criteria=body.acceptance_criteria,
            priority=body.priority,
            target_services=body.target_services,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return goal.model_dump(mode="json")


@router.get("")
def list_goals(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """List goals with optional filters."""
    try:
        goals = runtime.list_goals(status=status, priority=priority)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [g.model_dump(mode="json") for g in goals]


@router.get("/{goal_id}")
def get_goal(
    goal_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Get a goal and its tasks."""
    try:
        goal, tasks = runtime.show_goal(goal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "goal": goal.model_dump(mode="json"),
        "tasks": [t.model_dump(mode="json") for t in tasks],
    }


@router.post("/{goal_id}/activate")
def activate_goal(
    goal_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Activate a goal — decompose into tasks."""
    try:
        breakdown = runtime.activate_goal(goal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return breakdown.model_dump(mode="json")


@router.post("/{goal_id}/cancel")
def cancel_goal(
    goal_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Cancel a goal."""
    try:
        goal = runtime.cancel_goal(goal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return goal.model_dump(mode="json")
