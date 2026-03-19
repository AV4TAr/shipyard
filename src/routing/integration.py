"""RoutingBridge — connects the routing system to goals and pipelines."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.goals.models import AgentTask, TaskStatus

from .models import RouteDecision
from .router import TaskRouter


class RoutingBridge:
    """Bridges routing decisions with the goal/pipeline system.

    Parameters:
        router: The task router to use for agent selection.
        event_dispatcher: Optional dispatcher for routing event notifications.
    """

    def __init__(
        self,
        router: TaskRouter,
        event_dispatcher: Any | None = None,
    ) -> None:
        self._router = router
        self._event_dispatcher = event_dispatcher
        self._decisions: list[RouteDecision] = []

    @property
    def decisions(self) -> list[RouteDecision]:
        """Read-only access to all routing decisions made."""
        return list(self._decisions)

    def _fire_event(self, decision: RouteDecision) -> None:
        """Fire a routing event notification if a dispatcher is configured."""
        if self._event_dispatcher is None:
            return
        from src.notifications.models import Event, EventType

        event_type = (
            EventType.ROUTING_FALLBACK
            if decision.fallback_used
            else EventType.TASK_ROUTED
        )
        event = Event(
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            data={
                "task_id": decision.task_id,
                "agent_id": decision.selected_agent_id,
                "match_score": decision.match_score,
                "fallback_used": decision.fallback_used,
            },
        )
        self._event_dispatcher.dispatch(event)

    def route_and_assign(
        self,
        task: AgentTask,
        goal_manager: Any,
        pipeline_orchestrator: Any | None = None,
    ) -> RouteDecision:
        """Route a task and assign the selected agent.

        Routing only *assigns* an agent to the task — it does NOT kick off
        the pipeline.  The agent is responsible for submitting work via the
        SDK endpoint, which triggers the pipeline.

        Steps:
        1. Route the task to an agent.
        2. If an agent is selected, mark the task as ASSIGNED.
        3. If MANUAL or no agent found, leave the task PENDING for human
           assignment.

        Returns:
            The routing decision.
        """
        decision = self._router.route(task)
        self._decisions.append(decision)
        self._fire_event(decision)

        if decision.selected_agent_id is not None:
            # Mark task as assigned — the agent will submit work via the SDK.
            goal_manager.update_task_status(task.task_id, TaskStatus.ASSIGNED)
        # else: no agent selected — stays PENDING for manual handling.

        return decision

    def auto_route_goal(
        self,
        goal_id: uuid.UUID,
        goal_manager: Any,
        pipeline_orchestrator: Any,
    ) -> list[RouteDecision]:
        """Route all ready tasks for a goal, respecting dependencies.

        A task is 'ready' when it is PENDING and all its dependencies
        have been completed.

        Returns:
            List of routing decisions for tasks that were routed.
        """
        all_tasks = goal_manager.get_tasks(goal_id)
        completed_ids = {
            t.task_id for t in all_tasks if t.status == TaskStatus.COMPLETED
        }

        decisions: list[RouteDecision] = []
        for task in all_tasks:
            if task.status != TaskStatus.PENDING:
                continue
            # Check that all dependencies are completed.
            if not all(dep_id in completed_ids for dep_id in task.depends_on):
                continue
            decision = self.route_and_assign(
                task, goal_manager, pipeline_orchestrator
            )
            decisions.append(decision)

        return decisions
