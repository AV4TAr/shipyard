"""Project CRUD and lifecycle endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from src.cli.runtime import CLIRuntime

from ..dependencies import get_runtime
from ..schemas import GoalCreateRequest, MilestoneCreateRequest, ProjectCreateRequest, ProjectUpdateRequest

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
        # Set repo fields if provided
        if body.repo_url is not None:
            from pathlib import Path
            repo_path = Path(body.repo_url)
            # Validate local paths exist and are git repos
            if not body.repo_url.startswith(("http://", "https://", "git@")):
                if not repo_path.exists():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Repository path does not exist: {body.repo_url}",
                    )
                if not (repo_path / ".git").exists():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Not a git repository: {body.repo_url} (no .git directory)",
                    )
            project.repo_url = body.repo_url
        if body.default_branch is not None:
            project.default_branch = body.default_branch
        # Re-save if repo fields were set (they're not part of ProjectInput)
        if body.repo_url is not None or body.default_branch is not None:
            pm = runtime.project_manager
            pm._projects[project.project_id] = project
            if pm._project_repo:
                pm._project_repo.save(project)
    except HTTPException:
        raise
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


@router.get("/goal-map")
def goal_project_map(
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Return mapping of goal_id -> project info for all projects."""
    if runtime.project_manager is None:
        return {}
    return runtime.project_manager.goal_project_map()


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


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Delete a draft project."""
    try:
        import uuid as _uuid
        project = runtime.show_project(project_id)
        if project.status not in ("draft", "planning"):
            raise HTTPException(
                status_code=400,
                detail="Can only delete draft or planning projects",
            )
        pm = runtime.project_manager
        del pm._projects[_uuid.UUID(project_id)]
        if pm._project_repo:
            try:
                pm._project_repo.delete(_uuid.UUID(project_id))
            except Exception:
                pass
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except HTTPException:
        raise
    return {"deleted": True}


@router.patch("/{project_id}")
def update_project(
    project_id: str,
    body: ProjectUpdateRequest,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Update a project. Repo fields can be set on any status."""
    try:
        import uuid as _uuid
        pm = runtime.project_manager
        project = pm.get(_uuid.UUID(project_id))

        # Standard fields — only on draft/planning
        has_standard = body.title or body.description or body.priority
        if has_standard:
            if project.status in ("draft", "planning"):
                if body.title is not None:
                    project.title = body.title
                if body.description is not None:
                    project.description = body.description
                if body.priority is not None:
                    project.priority = body.priority
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Can only edit title/description/priority on draft projects",
                )

        # Repo fields — allowed on any status
        if body.repo_url is not None:
            from pathlib import Path
            repo_path = Path(body.repo_url)
            if not body.repo_url.startswith(("http://", "https://", "git@")):
                if not repo_path.exists():
                    raise HTTPException(
                        status_code=400,
                        detail="Repository path does not exist: {}".format(body.repo_url),
                    )
                if not (repo_path / ".git").exists():
                    raise HTTPException(
                        status_code=400,
                        detail="Not a git repository: {}".format(body.repo_url),
                    )
            project.repo_url = body.repo_url
        if body.default_branch is not None:
            project.default_branch = body.default_branch

        # Save
        pm._projects[project.project_id] = project
        if pm._project_repo:
            pm._project_repo.save(project)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return project.model_dump(mode="json")


@router.post("/{project_id}/milestones")
def add_milestone(
    project_id: str,
    body: MilestoneCreateRequest,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Add a milestone to a project."""
    try:
        import uuid as _uuid
        milestone = runtime.project_manager.add_milestone(
            _uuid.UUID(project_id),
            title=body.title,
            description=body.description,
            order=body.order,
            acceptance_criteria=body.acceptance_criteria,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return milestone.model_dump(mode="json")


@router.patch("/{project_id}/milestones/{milestone_id}")
def update_milestone(
    project_id: str,
    milestone_id: str,
    body: MilestoneCreateRequest,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Update a milestone's title and description."""
    try:
        import uuid as _uuid
        milestone = runtime.project_manager.update_milestone(
            _uuid.UUID(project_id),
            _uuid.UUID(milestone_id),
            title=body.title,
            description=body.description,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return milestone.model_dump(mode="json")


@router.delete("/{project_id}/milestones/{milestone_id}")
def delete_milestone(
    project_id: str,
    milestone_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Remove a milestone from a draft/planning project."""
    try:
        import uuid as _uuid
        runtime.project_manager.remove_milestone(
            _uuid.UUID(project_id),
            _uuid.UUID(milestone_id),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


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


@router.post("/{project_id}/pause")
def pause_project(
    project_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Pause an active project — hides its tasks from agents."""
    try:
        import uuid as _uuid
        project = runtime.project_manager.pause(_uuid.UUID(project_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return project.model_dump(mode="json")


@router.post("/{project_id}/resume")
def resume_project(
    project_id: str,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Resume a paused project — makes its tasks visible again."""
    try:
        import uuid as _uuid
        project = runtime.project_manager.resume(_uuid.UUID(project_id))
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
    """Cancel a project and expire all active leases for its tasks."""
    try:
        import uuid as _uuid
        pid = _uuid.UUID(project_id)

        # Expire active leases for all tasks in this project
        if runtime.lease_manager is not None and runtime.project_manager is not None:
            try:
                project = runtime.project_manager.get(pid)
                for ms in project.milestones:
                    for gid in ms.goal_ids:
                        try:
                            tasks = runtime.goal_manager.get_tasks(gid)
                            for task in tasks:
                                runtime.lease_manager.revoke(task.task_id)
                        except Exception:
                            pass
            except Exception:
                pass

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


@router.post("/{project_id}/milestones/{milestone_id}/goals")
def add_goal_to_project(
    project_id: str,
    milestone_id: str,
    body: GoalCreateRequest,
    runtime: CLIRuntime = Depends(get_runtime),
):
    """Create a goal and link it to a project milestone."""
    try:
        goal = runtime.add_goal_to_project(
            project_id,
            milestone_id,
            title=body.title,
            description=body.description,
            priority=body.priority,
            constraints=body.constraints,
            acceptance_criteria=body.acceptance_criteria,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return goal.model_dump(mode="json")
