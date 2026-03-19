"""Tests for the Projects Layer — models, manager, and planner."""

from __future__ import annotations

import uuid

import pytest

from src.goals.manager import GoalManager
from src.goals.models import GoalPriority, GoalStatus
from src.projects.manager import ProjectManager
from src.projects.models import (
    Milestone,
    MilestoneStatus,
    Project,
    ProjectInput,
    ProjectStatus,
)
from src.projects.planner import ProjectPlanner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_input() -> ProjectInput:
    return ProjectInput(
        title="Auth Revamp",
        description="Overhaul the authentication system",
        constraints=["No breaking changes to public API"],
        priority=GoalPriority.HIGH,
        target_services=["auth-service", "user-service"],
        tags=["security", "backend"],
    )


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


@pytest.fixture
def manager_with_goals() -> ProjectManager:
    return ProjectManager(goal_manager=GoalManager())


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestProjectStatus:
    def test_all_values(self):
        assert set(ProjectStatus) == {
            ProjectStatus.DRAFT,
            ProjectStatus.PLANNING,
            ProjectStatus.ACTIVE,
            ProjectStatus.PAUSED,
            ProjectStatus.COMPLETED,
            ProjectStatus.CANCELLED,
        }

    def test_string_values(self):
        assert ProjectStatus.DRAFT == "draft"
        assert ProjectStatus.PLANNING == "planning"
        assert ProjectStatus.ACTIVE == "active"
        assert ProjectStatus.PAUSED == "paused"
        assert ProjectStatus.COMPLETED == "completed"
        assert ProjectStatus.CANCELLED == "cancelled"


class TestMilestoneStatus:
    def test_all_values(self):
        assert set(MilestoneStatus) == {
            MilestoneStatus.PENDING,
            MilestoneStatus.ACTIVE,
            MilestoneStatus.COMPLETED,
            MilestoneStatus.BLOCKED,
        }

    def test_string_values(self):
        assert MilestoneStatus.PENDING == "pending"
        assert MilestoneStatus.ACTIVE == "active"
        assert MilestoneStatus.COMPLETED == "completed"
        assert MilestoneStatus.BLOCKED == "blocked"


class TestMilestoneModel:
    def test_create_milestone(self):
        m = Milestone(title="Foundation", order=0)
        assert m.title == "Foundation"
        assert m.order == 0
        assert m.status == MilestoneStatus.PENDING
        assert m.goal_ids == []
        assert m.acceptance_criteria == []
        assert m.description == ""
        assert isinstance(m.milestone_id, uuid.UUID)

    def test_milestone_serialization(self):
        m = Milestone(
            title="Phase 1",
            description="Setup",
            order=0,
            acceptance_criteria=["Tests pass"],
        )
        data = m.model_dump()
        assert data["title"] == "Phase 1"
        assert data["description"] == "Setup"
        assert data["order"] == 0
        assert data["acceptance_criteria"] == ["Tests pass"]

        restored = Milestone.model_validate(data)
        assert restored.milestone_id == m.milestone_id


class TestProjectInputModel:
    def test_create_with_defaults(self):
        inp = ProjectInput(title="Test", description="A test project")
        assert inp.priority == GoalPriority.MEDIUM
        assert inp.constraints == []
        assert inp.target_services == []
        assert inp.tags == []

    def test_create_with_all_fields(self, project_input: ProjectInput):
        assert project_input.title == "Auth Revamp"
        assert project_input.priority == GoalPriority.HIGH
        assert len(project_input.constraints) == 1
        assert "auth-service" in project_input.target_services


class TestProjectModel:
    def test_create_with_defaults(self):
        p = Project(title="My Project", description="Desc")
        assert p.status == ProjectStatus.DRAFT
        assert p.priority == GoalPriority.MEDIUM
        assert p.milestones == []
        assert p.metadata == {}
        assert p.created_by == ""
        assert isinstance(p.project_id, uuid.UUID)
        assert p.created_at is not None

    def test_serialization_roundtrip(self):
        p = Project(
            title="My Project",
            description="Full project",
            constraints=["no downtime"],
            tags=["infra"],
        )
        data = p.model_dump()
        restored = Project.model_validate(data)
        assert restored.project_id == p.project_id
        assert restored.title == p.title
        assert restored.constraints == ["no downtime"]


# ---------------------------------------------------------------------------
# ProjectManager tests
# ---------------------------------------------------------------------------


class TestProjectManagerCreate:
    def test_create_from_input(self, manager: ProjectManager, project_input: ProjectInput):
        project = manager.create(project_input, created_by="alice")
        assert project.title == "Auth Revamp"
        assert project.status == ProjectStatus.DRAFT
        assert project.created_by == "alice"
        assert project.priority == GoalPriority.HIGH
        assert project.constraints == ["No breaking changes to public API"]
        assert project.target_services == ["auth-service", "user-service"]
        assert project.tags == ["security", "backend"]

    def test_create_assigns_unique_ids(self, manager: ProjectManager):
        inp = ProjectInput(title="A", description="a")
        p1 = manager.create(inp)
        p2 = manager.create(inp)
        assert p1.project_id != p2.project_id


class TestProjectManagerGetList:
    def test_get_existing(self, manager: ProjectManager):
        inp = ProjectInput(title="X", description="x")
        p = manager.create(inp)
        assert manager.get(p.project_id) is p

    def test_get_nonexistent(self, manager: ProjectManager):
        with pytest.raises(KeyError, match="not found"):
            manager.get(uuid.uuid4())

    def test_list_all(self, manager: ProjectManager):
        manager.create(ProjectInput(title="A", description="a"))
        manager.create(ProjectInput(title="B", description="b"))
        assert len(manager.list_projects()) == 2

    def test_list_filtered_by_status(self, manager: ProjectManager):
        p1 = manager.create(ProjectInput(title="A", description="a"))
        manager.create(ProjectInput(title="B", description="b"))
        manager.plan(p1.project_id)

        drafts = manager.list_projects(status=ProjectStatus.DRAFT)
        assert len(drafts) == 1
        planning = manager.list_projects(status=ProjectStatus.PLANNING)
        assert len(planning) == 1


class TestProjectManagerMilestones:
    def test_add_milestone(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        m = manager.add_milestone(p.project_id, title="Phase 1", description="First")
        assert m.title == "Phase 1"
        assert m.order == 0
        assert len(p.milestones) == 1

    def test_add_milestone_auto_order(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        m1 = manager.add_milestone(p.project_id, title="A")
        m2 = manager.add_milestone(p.project_id, title="B")
        assert m1.order == 0
        assert m2.order == 1

    def test_add_milestone_explicit_order(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.add_milestone(p.project_id, title="B", order=1)
        manager.add_milestone(p.project_id, title="A", order=0)
        # Sorted by order
        assert p.milestones[0].title == "A"
        assert p.milestones[1].title == "B"

    def test_add_milestone_with_criteria(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        m = manager.add_milestone(
            p.project_id,
            title="M",
            acceptance_criteria=["Tests pass", "Docs done"],
        )
        assert m.acceptance_criteria == ["Tests pass", "Docs done"]

    def test_add_milestone_nonexistent_project(self, manager: ProjectManager):
        with pytest.raises(KeyError):
            manager.add_milestone(uuid.uuid4(), title="M")


class TestProjectManagerLifecycle:
    def test_plan_from_draft(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        result = manager.plan(p.project_id)
        assert result.status == ProjectStatus.PLANNING

    def test_plan_from_active_raises(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M")
        manager.activate(p.project_id)
        with pytest.raises(ValueError, match="Cannot transition"):
            manager.plan(p.project_id)

    def test_activate_from_planning(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="First")
        result = manager.activate(p.project_id)
        assert result.status == ProjectStatus.ACTIVE
        assert p.milestones[0].status == MilestoneStatus.ACTIVE

    def test_activate_without_milestones_raises(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        with pytest.raises(ValueError, match="no milestones"):
            manager.activate(p.project_id)

    def test_activate_from_draft_raises(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.add_milestone(p.project_id, title="M")
        with pytest.raises(ValueError, match="Cannot transition"):
            manager.activate(p.project_id)


    def test_cancel_from_draft(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        result = manager.cancel(p.project_id)
        assert result.status == ProjectStatus.CANCELLED

    def test_cancel_from_active(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M")
        manager.activate(p.project_id)
        result = manager.cancel(p.project_id)
        assert result.status == ProjectStatus.CANCELLED

    def test_cancel_completed_raises(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M")
        manager.activate(p.project_id)
        manager.complete_milestone(p.project_id, p.milestones[0].milestone_id)
        # Project is now completed (single milestone)
        assert p.status == ProjectStatus.COMPLETED
        with pytest.raises(ValueError, match="Cannot transition"):
            manager.cancel(p.project_id)

    def test_pause_active(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M")
        manager.activate(p.project_id)
        result = manager.pause(p.project_id)
        assert result.status == ProjectStatus.PAUSED

    def test_pause_draft_raises(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        with pytest.raises(ValueError, match="Cannot transition"):
            manager.pause(p.project_id)

    def test_resume_paused(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M")
        manager.activate(p.project_id)
        manager.pause(p.project_id)
        result = manager.resume(p.project_id)
        assert result.status == ProjectStatus.ACTIVE


class TestProjectManagerMilestoneCompletion:
    def test_complete_milestone(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M1")
        manager.add_milestone(p.project_id, title="M2")
        manager.activate(p.project_id)

        m1 = p.milestones[0]
        result = manager.complete_milestone(p.project_id, m1.milestone_id)
        assert result.status == MilestoneStatus.COMPLETED

    def test_complete_milestone_auto_activates_next(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M1")
        manager.add_milestone(p.project_id, title="M2")
        manager.activate(p.project_id)

        m1 = p.milestones[0]
        m2 = p.milestones[1]
        manager.complete_milestone(p.project_id, m1.milestone_id)
        assert m2.status == MilestoneStatus.ACTIVE

    def test_complete_last_milestone_completes_project(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M1")
        manager.activate(p.project_id)

        m1 = p.milestones[0]
        manager.complete_milestone(p.project_id, m1.milestone_id)
        assert p.status == ProjectStatus.COMPLETED

    def test_complete_pending_milestone_raises(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M1")
        manager.add_milestone(p.project_id, title="M2")
        manager.activate(p.project_id)

        m2 = p.milestones[1]
        with pytest.raises(ValueError, match="must be active"):
            manager.complete_milestone(p.project_id, m2.milestone_id)

    def test_complete_nonexistent_milestone_raises(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M1")
        manager.activate(p.project_id)

        with pytest.raises(KeyError, match="Milestone"):
            manager.complete_milestone(p.project_id, uuid.uuid4())


class TestProjectManagerActivateNextMilestone:
    def test_activate_next(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M1")
        manager.add_milestone(p.project_id, title="M2")
        manager.activate(p.project_id)

        # M1 is already active; manually complete it
        p.milestones[0].status = MilestoneStatus.COMPLETED

        result = manager.activate_next_milestone(p.project_id)
        assert result is not None
        assert result.title == "M2"
        assert result.status == MilestoneStatus.ACTIVE

    def test_activate_next_none_remaining(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M1")
        manager.activate(p.project_id)

        # No pending milestones left
        result = manager.activate_next_milestone(p.project_id)
        assert result is None


class TestProjectManagerProgress:
    def test_progress_empty(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        progress = manager.get_progress(p.project_id)
        assert progress["milestones_total"] == 0
        assert progress["milestones_completed"] == 0
        assert progress["milestones_active"] == 0
        assert progress["milestones_pending"] == 0
        assert progress["milestones_blocked"] == 0
        assert progress["total_goals"] == 0
        assert progress["status"] == "draft"
        assert progress["project_id"] == str(p.project_id)

    def test_progress_with_milestones(self, manager: ProjectManager):
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M1")
        manager.add_milestone(p.project_id, title="M2")
        manager.add_milestone(p.project_id, title="M3")
        manager.activate(p.project_id)

        progress = manager.get_progress(p.project_id)
        assert progress["milestones_total"] == 3
        assert progress["milestones_active"] == 1
        assert progress["milestones_pending"] == 2

    def test_progress_nonexistent_raises(self, manager: ProjectManager):
        with pytest.raises(KeyError):
            manager.get_progress(uuid.uuid4())


# ---------------------------------------------------------------------------
# ProjectPlanner tests
# ---------------------------------------------------------------------------


class TestProjectPlanner:
    def test_generates_three_milestones(self):
        planner = ProjectPlanner()
        project = Project(title="Widget", description="Build a widget")
        milestones = planner.plan(project)
        assert len(milestones) == 3

    def test_milestone_ordering(self):
        planner = ProjectPlanner()
        project = Project(title="Widget", description="Build a widget")
        milestones = planner.plan(project)
        orders = [m.order for m in milestones]
        assert orders == [0, 1, 2]

    def test_milestone_titles(self):
        planner = ProjectPlanner()
        project = Project(title="Widget", description="Build a widget")
        milestones = planner.plan(project)
        titles = [m.title for m in milestones]
        assert titles == ["Foundation", "Implementation", "Polish"]

    def test_milestone_statuses_are_pending(self):
        planner = ProjectPlanner()
        project = Project(title="Widget", description="Build a widget")
        milestones = planner.plan(project)
        assert all(m.status == MilestoneStatus.PENDING for m in milestones)

    def test_acceptance_criteria_present(self):
        planner = ProjectPlanner()
        project = Project(title="Widget", description="Build a widget")
        milestones = planner.plan(project)
        for m in milestones:
            assert len(m.acceptance_criteria) > 0

    def test_target_services_in_foundation_criteria(self):
        planner = ProjectPlanner()
        project = Project(
            title="Widget",
            description="Build a widget",
            target_services=["svc-a", "svc-b"],
        )
        milestones = planner.plan(project)
        foundation = milestones[0]
        criteria_text = " ".join(foundation.acceptance_criteria)
        assert "svc-a" in criteria_text
        assert "svc-b" in criteria_text

    def test_constraints_in_implementation_criteria(self):
        planner = ProjectPlanner()
        project = Project(
            title="Widget",
            description="Build a widget",
            constraints=["No downtime"],
        )
        milestones = planner.plan(project)
        impl = milestones[1]
        criteria_text = " ".join(impl.acceptance_criteria)
        assert "constraints" in criteria_text.lower()


# ---------------------------------------------------------------------------
# Integration: ProjectManager + GoalManager
# ---------------------------------------------------------------------------


class TestProjectGoalIntegration:
    def test_activate_creates_goal(self, manager_with_goals: ProjectManager):
        mgr = manager_with_goals
        inp = ProjectInput(
            title="Auth",
            description="Auth system",
            constraints=["No breaking changes"],
            priority=GoalPriority.HIGH,
            target_services=["auth"],
        )
        p = mgr.create(inp, created_by="alice")
        mgr.plan(p.project_id)
        mgr.add_milestone(p.project_id, title="Foundation", description="Setup base")
        mgr.activate(p.project_id)

        # Milestone should have a goal_id
        m = p.milestones[0]
        assert len(m.goal_ids) == 1

        # Goal should exist in GoalManager
        goal_mgr = mgr._goal_manager
        assert goal_mgr is not None
        goal = goal_mgr.get(m.goal_ids[0])
        assert goal.status == GoalStatus.ACTIVE
        assert goal.priority == GoalPriority.HIGH
        assert "Auth" in goal.title
        assert goal.created_by == "alice"
        assert goal.constraints == ["No breaking changes"]
        assert goal.target_services == ["auth"]

    def test_complete_milestone_creates_next_goal(
        self, manager_with_goals: ProjectManager
    ):
        mgr = manager_with_goals
        inp = ProjectInput(title="P", description="p")
        p = mgr.create(inp)
        mgr.plan(p.project_id)
        mgr.add_milestone(p.project_id, title="M1", description="First phase")
        mgr.add_milestone(p.project_id, title="M2", description="Second phase")
        mgr.activate(p.project_id)

        m1 = p.milestones[0]
        m2 = p.milestones[1]
        assert len(m1.goal_ids) == 1
        assert len(m2.goal_ids) == 0

        mgr.complete_milestone(p.project_id, m1.milestone_id)
        assert len(m2.goal_ids) == 1

    def test_no_goal_manager_still_works(self, manager: ProjectManager):
        """ProjectManager works without a GoalManager — just no goals created."""
        p = manager.create(ProjectInput(title="P", description="p"))
        manager.plan(p.project_id)
        manager.add_milestone(p.project_id, title="M1")
        manager.activate(p.project_id)
        assert p.milestones[0].status == MilestoneStatus.ACTIVE
        assert p.milestones[0].goal_ids == []
