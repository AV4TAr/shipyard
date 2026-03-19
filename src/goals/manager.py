"""GoalManager — goal and task lifecycle management with optional persistence."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from .decomposer import GoalDecomposer
from .models import (
    AgentTask,
    Goal,
    GoalInput,
    GoalPriority,
    GoalStatus,
    TaskBreakdown,
    TaskStatus,
)

if TYPE_CHECKING:
    from src.storage.repositories import GoalRepository, TaskRepository

logger = logging.getLogger(__name__)


class GoalManager:
    """Creates, stores, and manages :class:`Goal` and :class:`AgentTask` lifecycles.

    When *goal_repo* and/or *task_repo* are provided, the manager delegates
    storage to those repositories.  Otherwise it falls back to internal dicts
    for full backward compatibility.
    """

    def __init__(
        self,
        decomposer: GoalDecomposer | None = None,
        *,
        goal_repo: GoalRepository | None = None,
        task_repo: TaskRepository | None = None,
        event_dispatcher: Any | None = None,
        on_goal_completed: Any | None = None,
    ) -> None:
        self._decomposer = decomposer or GoalDecomposer()
        self._goal_repo = goal_repo
        self._task_repo = task_repo
        self._event_dispatcher = event_dispatcher
        self._on_goal_completed = on_goal_completed
        # Internal dicts as fallback when no repos are provided
        self._goals: dict[uuid.UUID, Goal] = {}
        self._breakdowns: dict[uuid.UUID, TaskBreakdown] = {}
        self._tasks: dict[uuid.UUID, AgentTask] = {}

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _save_goal(self, goal: Goal) -> None:
        # Write to repo first (source of truth), then update cache
        if self._goal_repo:
            self._goal_repo.save(goal)
        self._goals[goal.goal_id] = goal

    def _get_goal(self, goal_id: uuid.UUID) -> Goal:
        # Repo is source of truth when available
        if self._goal_repo:
            goal = self._goal_repo.get(goal_id)
            if goal is not None:
                self._goals[goal_id] = goal  # update cache
                return goal
        # Fall back to memory
        if goal_id in self._goals:
            return self._goals[goal_id]
        raise KeyError(f"Goal {goal_id} not found")

    def _list_goals(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
    ) -> list[Goal]:
        if self._goal_repo:
            goals = self._goal_repo.list_all(status=status, priority=priority)
            # Update cache
            for g in goals:
                self._goals[g.goal_id] = g
            return goals
        results = list(self._goals.values())
        if status is not None:
            results = [g for g in results if g.status == status]
        if priority is not None:
            results = [g for g in results if g.priority == priority]
        return results

    def _save_task(self, task: AgentTask) -> None:
        # Write to repo first (source of truth), then update cache
        if self._task_repo:
            self._task_repo.save(task)
        self._tasks[task.task_id] = task

    def _get_tasks_for_goal(self, goal_id: uuid.UUID) -> list[AgentTask]:
        if self._task_repo:
            tasks = self._task_repo.list_by_goal(goal_id)
            # Update cache
            for t in tasks:
                self._tasks[t.task_id] = t
            return tasks
        breakdown = self._breakdowns.get(goal_id)
        if breakdown is None:
            return []
        return list(breakdown.tasks)

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
        self._save_goal(goal)
        self._dispatch("goal.created", {
            "goal_id": str(goal.goal_id),
            "title": goal.title,
            "priority": goal.priority.value,
            "created_by": created_by,
        })
        return goal

    def get(self, goal_id: uuid.UUID) -> Goal:
        """Retrieve a goal by ID.

        Raises:
            KeyError: If the goal does not exist.
        """
        return self._get_goal(goal_id)

    def list_goals(
        self,
        status: Optional[GoalStatus] = None,
        priority: Optional["GoalStatus"] = None,  # actually GoalPriority; avoids circular
    ) -> list[Goal]:
        """List all goals, optionally filtered by status and/or priority."""
        return self._list_goals(status=status, priority=priority)

    def cancel(self, goal_id: uuid.UUID) -> Goal:
        """Cancel a goal and all its pending/assigned tasks.

        Raises:
            KeyError: If the goal does not exist.
        """
        goal = self.get(goal_id)
        goal.status = GoalStatus.CANCELLED
        self._save_goal(goal)

        # Cancel outstanding tasks
        tasks = self._get_tasks_for_goal(goal_id)
        for task in tasks:
            if task.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                task.status = TaskStatus.FAILED
                self._save_task(task)

        # Also update breakdown tasks in memory (for backward compat, no-repo mode only)
        if not self._task_repo and goal_id in self._breakdowns:
            for task in self._breakdowns[goal_id].tasks:
                if task.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                    task.status = TaskStatus.FAILED

        return goal

    def complete(self, goal_id: uuid.UUID) -> Goal:
        """Mark a goal as completed.

        Raises:
            KeyError: If the goal does not exist.
        """
        goal = self.get(goal_id)
        goal.status = GoalStatus.COMPLETED
        self._save_goal(goal)
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
        self._save_goal(goal)

        breakdown = self._decomposer.decompose(goal)
        if not self._task_repo:
            self._breakdowns[goal_id] = breakdown

        # Index every task for fast lookup and persist
        for task in breakdown.tasks:
            self._save_task(task)

        self._dispatch("goal.activated", {
            "goal_id": str(goal_id),
            "title": goal.title,
            "task_count": len(breakdown.tasks),
        })

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
        return self._get_tasks_for_goal(goal_id)

    def update_task_status(self, task_id: uuid.UUID, status: TaskStatus) -> AgentTask:
        """Update the status of a task.

        When all tasks for a goal become COMPLETED, the goal is
        automatically marked COMPLETED as well.

        Raises:
            KeyError: If the task does not exist.
        """
        # Repo is source of truth when available, then fall back to cache
        task: AgentTask | None = None
        if self._task_repo:
            task = self._task_repo.get(task_id)
            if task is not None:
                self._tasks[task_id] = task  # update cache
        if task is None:
            task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")

        task.status = status
        self._save_task(task)

        # If a task is now IN_PROGRESS, the parent goal should be too
        try:
            goal = self._get_goal(task.goal_id)
            if status == TaskStatus.IN_PROGRESS and goal.status == GoalStatus.ACTIVE:
                goal.status = GoalStatus.IN_PROGRESS
                self._save_goal(goal)
        except KeyError:
            pass

        # Auto-complete the goal when every task is COMPLETED
        self._check_auto_complete(task.goal_id)

        return task

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_auto_complete(self, goal_id: uuid.UUID) -> None:
        """If all tasks for *goal_id* are COMPLETED, mark the goal COMPLETED."""
        tasks = self._get_tasks_for_goal(goal_id)
        if not tasks:
            return
        if all(t.status == TaskStatus.COMPLETED for t in tasks):
            try:
                goal = self._get_goal(goal_id)
                if goal.status != GoalStatus.CANCELLED:
                    goal.status = GoalStatus.COMPLETED
                    self._save_goal(goal)
                    self._dispatch("goal.completed", {
                        "goal_id": str(goal_id),
                        "title": goal.title,
                    })
                    # Notify completion callback (used by ProjectManager)
                    if self._on_goal_completed:
                        try:
                            self._on_goal_completed(goal_id)
                        except Exception:
                            pass
            except KeyError:
                pass

    # ------------------------------------------------------------------
    # Event dispatch helper
    # ------------------------------------------------------------------

    def _dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        """Fire a notification event if a dispatcher is configured."""
        if self._event_dispatcher is None:
            return
        try:
            from src.notifications.models import Event, EventType

            event = Event(
                event_type=EventType(event_type),
                timestamp=datetime.now(timezone.utc),
                data=data,
            )
            self._event_dispatcher.dispatch(event)
        except Exception:
            logger.debug("Failed to dispatch event %s", event_type, exc_info=True)
