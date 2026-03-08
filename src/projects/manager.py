"""ProjectManager — in-memory project and milestone lifecycle management."""

from __future__ import annotations

import uuid

from src.goals.manager import GoalManager
from src.goals.models import GoalInput

from .models import (
    Milestone,
    MilestoneStatus,
    Project,
    ProjectInput,
    ProjectStatus,
)

# Valid state transitions for projects.
_VALID_TRANSITIONS: dict[ProjectStatus, set[ProjectStatus]] = {
    ProjectStatus.DRAFT: {ProjectStatus.PLANNING, ProjectStatus.CANCELLED},
    ProjectStatus.PLANNING: {ProjectStatus.ACTIVE, ProjectStatus.CANCELLED},
    ProjectStatus.ACTIVE: {
        ProjectStatus.PAUSED,
        ProjectStatus.COMPLETED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.PAUSED: {ProjectStatus.ACTIVE, ProjectStatus.CANCELLED},
    ProjectStatus.COMPLETED: set(),
    ProjectStatus.CANCELLED: set(),
}


class ProjectManager:
    """Creates, stores, and manages :class:`Project` and :class:`Milestone` lifecycles.

    All state is kept in-memory (dicts keyed by ID).
    """

    def __init__(self, goal_manager: GoalManager | None = None) -> None:
        self._projects: dict[uuid.UUID, Project] = {}
        self._goal_manager = goal_manager

    # ------------------------------------------------------------------
    # Project CRUD
    # ------------------------------------------------------------------

    def create(self, input: ProjectInput, *, created_by: str = "") -> Project:
        """Create a new project from input. Starts in DRAFT status."""
        project = Project(
            title=input.title,
            description=input.description,
            constraints=list(input.constraints),
            priority=input.priority,
            target_services=list(input.target_services),
            tags=list(input.tags),
            created_by=created_by,
        )
        self._projects[project.project_id] = project
        return project

    def get(self, project_id: uuid.UUID) -> Project:
        """Retrieve a project by ID.

        Raises:
            KeyError: If the project does not exist.
        """
        try:
            return self._projects[project_id]
        except KeyError:
            raise KeyError(f"Project {project_id} not found")

    def list_projects(self, status: ProjectStatus | None = None) -> list[Project]:
        """List all projects, optionally filtered by status."""
        results = list(self._projects.values())
        if status is not None:
            results = [p for p in results if p.status == status]
        return results

    # ------------------------------------------------------------------
    # Milestone management
    # ------------------------------------------------------------------

    def add_milestone(
        self,
        project_id: uuid.UUID,
        *,
        title: str,
        description: str = "",
        order: int | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> Milestone:
        """Add a milestone to a project.

        If *order* is not provided, the milestone is appended after existing ones.

        Raises:
            KeyError: If the project does not exist.
        """
        project = self.get(project_id)

        if order is None:
            order = len(project.milestones)

        milestone = Milestone(
            title=title,
            description=description,
            order=order,
            acceptance_criteria=list(acceptance_criteria or []),
        )
        project.milestones.append(milestone)
        # Keep milestones sorted by order
        project.milestones.sort(key=lambda m: m.order)
        return milestone

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _transition(self, project_id: uuid.UUID, target: ProjectStatus) -> Project:
        """Transition a project to *target* status, validating the transition.

        Raises:
            KeyError: If the project does not exist.
            ValueError: If the transition is not allowed.
        """
        project = self.get(project_id)
        if target not in _VALID_TRANSITIONS.get(project.status, set()):
            raise ValueError(
                f"Cannot transition project from {project.status.value} "
                f"to {target.value}"
            )
        project.status = target
        return project

    def plan(self, project_id: uuid.UUID) -> Project:
        """Move project from DRAFT to PLANNING status."""
        return self._transition(project_id, ProjectStatus.PLANNING)

    def activate(self, project_id: uuid.UUID) -> Project:
        """Move project to ACTIVE. Creates goals for the first pending milestone.

        Raises:
            KeyError: If the project does not exist.
            ValueError: If the transition is not allowed or no milestones exist.
        """
        project = self.get(project_id)

        # Validate transition first, then check milestones
        if ProjectStatus.ACTIVE not in _VALID_TRANSITIONS.get(project.status, set()):
            raise ValueError(
                f"Cannot transition project from {project.status.value} "
                f"to {ProjectStatus.ACTIVE.value}"
            )

        if not project.milestones:
            raise ValueError("Cannot activate a project with no milestones")

        project.status = ProjectStatus.ACTIVE

        # Activate the first pending milestone
        self._activate_milestone(project, self._first_pending(project))

        return project

    def activate_next_milestone(
        self, project_id: uuid.UUID
    ) -> Milestone | None:
        """Activate the next pending milestone, creating its goals.

        Returns None if there are no more pending milestones.

        Raises:
            KeyError: If the project does not exist.
        """
        project = self.get(project_id)
        milestone = self._first_pending(project)
        if milestone is None:
            return None
        self._activate_milestone(project, milestone)
        return milestone

    def complete_milestone(
        self, project_id: uuid.UUID, milestone_id: uuid.UUID
    ) -> Milestone:
        """Mark a milestone as completed. Auto-activates next if available.

        Raises:
            KeyError: If the project or milestone does not exist.
            ValueError: If the milestone is not currently active.
        """
        project = self.get(project_id)
        milestone = self._get_milestone(project, milestone_id)

        if milestone.status != MilestoneStatus.ACTIVE:
            raise ValueError(
                f"Cannot complete milestone in {milestone.status.value} status; "
                "must be active"
            )

        milestone.status = MilestoneStatus.COMPLETED

        # Auto-activate next pending milestone
        next_ms = self._first_pending(project)
        if next_ms is not None:
            self._activate_milestone(project, next_ms)
        else:
            # All milestones done — complete the project
            all_completed = all(
                m.status == MilestoneStatus.COMPLETED for m in project.milestones
            )
            if all_completed:
                project.status = ProjectStatus.COMPLETED

        return milestone

    def cancel(self, project_id: uuid.UUID) -> Project:
        """Cancel a project."""
        return self._transition(project_id, ProjectStatus.CANCELLED)

    def pause(self, project_id: uuid.UUID) -> Project:
        """Pause an active project."""
        return self._transition(project_id, ProjectStatus.PAUSED)

    def resume(self, project_id: uuid.UUID) -> Project:
        """Resume a paused project back to ACTIVE."""
        return self._transition(project_id, ProjectStatus.ACTIVE)

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------

    def get_progress(self, project_id: uuid.UUID) -> dict:
        """Return progress summary for a project.

        Raises:
            KeyError: If the project does not exist.
        """
        project = self.get(project_id)

        total = len(project.milestones)
        completed = sum(
            1 for m in project.milestones if m.status == MilestoneStatus.COMPLETED
        )
        active = sum(
            1 for m in project.milestones if m.status == MilestoneStatus.ACTIVE
        )
        pending = sum(
            1 for m in project.milestones if m.status == MilestoneStatus.PENDING
        )
        blocked = sum(
            1 for m in project.milestones if m.status == MilestoneStatus.BLOCKED
        )

        # Count goals across all milestones
        all_goal_ids: list[uuid.UUID] = []
        for m in project.milestones:
            all_goal_ids.extend(m.goal_ids)

        return {
            "project_id": str(project.project_id),
            "status": project.status.value,
            "milestones_total": total,
            "milestones_completed": completed,
            "milestones_active": active,
            "milestones_pending": pending,
            "milestones_blocked": blocked,
            "total_goals": len(all_goal_ids),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _first_pending(self, project: Project) -> Milestone | None:
        """Return the first milestone in PENDING status (by order)."""
        for m in sorted(project.milestones, key=lambda ms: ms.order):
            if m.status == MilestoneStatus.PENDING:
                return m
        return None

    def _get_milestone(
        self, project: Project, milestone_id: uuid.UUID
    ) -> Milestone:
        """Find a milestone within a project by ID.

        Raises:
            KeyError: If the milestone does not exist.
        """
        for m in project.milestones:
            if m.milestone_id == milestone_id:
                return m
        raise KeyError(f"Milestone {milestone_id} not found in project {project.project_id}")

    def _activate_milestone(
        self, project: Project, milestone: Milestone | None
    ) -> None:
        """Set a milestone to ACTIVE and create goals for it via GoalManager."""
        if milestone is None:
            return

        milestone.status = MilestoneStatus.ACTIVE

        if self._goal_manager is not None:
            # Create a goal for this milestone
            goal_input = GoalInput(
                title=f"{project.title}: {milestone.title}",
                description=milestone.description or f"Work for milestone: {milestone.title}",
                constraints=list(project.constraints),
                acceptance_criteria=list(milestone.acceptance_criteria),
                priority=project.priority,
                target_services=list(project.target_services),
            )
            goal = self._goal_manager.create(goal_input, created_by=project.created_by)
            milestone.goal_ids.append(goal.goal_id)
