"""GoalManager — in-memory goal and task lifecycle management."""

from __future__ import annotations

import uuid
from typing import Optional

from .decomposer import GoalDecomposer
from .models import (
    AgentTask,
    Goal,
    GoalInput,
    GoalStatus,
    TaskBreakdown,
    TaskStatus,
)


class GoalManager:
    """Creates, stores, and manages :class:`Goal` and :class:`AgentTask` lifecycles.

    All state is kept in-memory (dicts keyed by ID).
    """

    def __init__(self, decomposer: GoalDecomposer | None = None) -> None:
        self._goals: dict[uuid.UUID, Goal] = {}
        self._breakdowns: dict[uuid.UUID, TaskBreakdown] = {}
        self._tasks: dict[uuid.UUID, AgentTask] = {}
        self._decomposer = decomposer or GoalDecomposer()

    # ------------------------------------------------------------------
    # Goal CRUD
    # ------------------------------------------------------------------

    def create(self, input: GoalInput, created_by: str) -> Goal:
        """Create a new goal from minimal human input.

        The goal starts in DRAFT status. Call :meth:`activate` to decompose
        it into tasks and make it ready for agents.
        """
        goal = Goal(
            title=input.title,
            description=input.description,
            constraints=list(input.constraints),
            acceptance_criteria=list(input.acceptance_criteria),
            priority=input.priority,
            target_services=list(input.target_services),
            created_by=created_by,
            status=GoalStatus.DRAFT,
        )
        self._goals[goal.goal_id] = goal
        return goal

    def get(self, goal_id: uuid.UUID) -> Goal:
        """Retrieve a goal by ID.

        Raises:
            KeyError: If the goal does not exist.
        """
        try:
            return self._goals[goal_id]
        except KeyError:
            raise KeyError(f"Goal {goal_id} not found")

    def list_goals(
        self,
        status: Optional[GoalStatus] = None,
        priority: Optional["GoalStatus"] = None,  # actually GoalPriority; avoids circular
    ) -> list[Goal]:
        """List all goals, optionally filtered by status and/or priority."""
        results = list(self._goals.values())
        if status is not None:
            results = [g for g in results if g.status == status]
        if priority is not None:
            results = [g for g in results if g.priority == priority]
        return results

    def cancel(self, goal_id: uuid.UUID) -> Goal:
        """Cancel a goal and all its pending/assigned tasks.

        Raises:
            KeyError: If the goal does not exist.
        """
        goal = self.get(goal_id)
        goal.status = GoalStatus.CANCELLED

        # Cancel outstanding tasks
        if goal_id in self._breakdowns:
            for task in self._breakdowns[goal_id].tasks:
                if task.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                    task.status = TaskStatus.FAILED
                    # Also update the task index
                    if task.task_id in self._tasks:
                        self._tasks[task.task_id].status = TaskStatus.FAILED

        return goal

    def complete(self, goal_id: uuid.UUID) -> Goal:
        """Mark a goal as completed.

        Raises:
            KeyError: If the goal does not exist.
        """
        goal = self.get(goal_id)
        goal.status = GoalStatus.COMPLETED
        return goal

    # ------------------------------------------------------------------
    # Activation / decomposition
    # ------------------------------------------------------------------

    def activate(self, goal_id: uuid.UUID) -> TaskBreakdown:
        """Decompose the goal into tasks and mark it as ACTIVE.

        Raises:
            KeyError: If the goal does not exist.
        """
        goal = self.get(goal_id)
        goal.status = GoalStatus.ACTIVE

        breakdown = self._decomposer.decompose(goal)
        self._breakdowns[goal_id] = breakdown

        # Index every task for fast lookup
        for task in breakdown.tasks:
            self._tasks[task.task_id] = task

        return breakdown

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    def get_tasks(self, goal_id: uuid.UUID) -> list[AgentTask]:
        """Return all tasks for a given goal.

        Raises:
            KeyError: If the goal does not exist.
        """
        self.get(goal_id)  # verify goal exists
        breakdown = self._breakdowns.get(goal_id)
        if breakdown is None:
            return []
        return list(breakdown.tasks)

    def update_task_status(self, task_id: uuid.UUID, status: TaskStatus) -> AgentTask:
        """Update the status of a task.

        When all tasks for a goal become COMPLETED, the goal is
        automatically marked COMPLETED as well.

        Raises:
            KeyError: If the task does not exist.
        """
        if task_id not in self._tasks:
            raise KeyError(f"Task {task_id} not found")

        task = self._tasks[task_id]
        task.status = status

        # If a task is now IN_PROGRESS, the parent goal should be too
        goal = self._goals.get(task.goal_id)
        if goal and status == TaskStatus.IN_PROGRESS and goal.status == GoalStatus.ACTIVE:
            goal.status = GoalStatus.IN_PROGRESS

        # Auto-complete the goal when every task is COMPLETED
        self._check_auto_complete(task.goal_id)

        return task

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_auto_complete(self, goal_id: uuid.UUID) -> None:
        """If all tasks for *goal_id* are COMPLETED, mark the goal COMPLETED."""
        breakdown = self._breakdowns.get(goal_id)
        if breakdown is None:
            return
        if all(t.status == TaskStatus.COMPLETED for t in breakdown.tasks):
            goal = self._goals.get(goal_id)
            if goal and goal.status != GoalStatus.CANCELLED:
                goal.status = GoalStatus.COMPLETED
