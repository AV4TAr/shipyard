"""Project CRUD and lifecycle endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from src.cli.runtime import CLIRuntime

from ..dependencies import get_runtime
from ..schemas import ProjectCreateRequest

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("")
def create_project(
    body: ProjectCreateRequest,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Create a new project."""
    try:
        project = runtime.create_project(
            title=body.title,
            description=body.description,
            constraints=body.constraints,
            priority=body.priority,
            target_services=body.target_services,
            tags=body.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return project.model_dump(mode="json")


@router.get("")
def list_projects(
    status: Optional[str] = None,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """List projects with optional status filter."""
    try:
        projects = runtime.list_projects(status=status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [p.model_dump(mode="json") for p in projects]


@router.get("/{project_id}")
def get_project(
    project_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Get project details with milestones."""
    try:
        project = runtime.show_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return project.model_dump(mode="json")


@router.post("/{project_id}/activate")
def activate_project(
    project_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Activate a project — plan milestones and start execution."""
    try:
        project = runtime.activate_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return project.model_dump(mode="json")


@router.post("/{project_id}/cancel")
def cancel_project(
    project_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Cancel a project."""
    try:
        project = runtime.cancel_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return project.model_dump(mode="json")


@router.get("/{project_id}/milestones")
def list_milestones(
    project_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """List milestones for a project."""
    try:
        milestones = runtime.list_project_milestones(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return [m.model_dump(mode="json") for m in milestones]


@router.post("/{project_id}/milestones/{milestone_id}/complete")
def complete_milestone(
    project_id: str,
    milestone_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Complete a milestone within a project."""
    try:
        milestone = runtime.complete_milestone(project_id, milestone_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return milestone.model_dump(mode="json")


@router.get("/{project_id}/goals")
def list_project_goals(
    project_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """List goals linked to a project."""
    try:
        goals = runtime.list_project_goals(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return [g.model_dump(mode="json") for g in goals]
