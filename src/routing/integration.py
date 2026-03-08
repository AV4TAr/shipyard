"""RoutingBridge — connects the routing system to goals and pipelines."""

from __future__ import annotations

import uuid
from typing import Any

from src.goals.models import AgentTask, TaskStatus
from src.intent.schema import IntentDeclaration

from .models import RouteDecision, RoutingStrategy
from .router import TaskRouter


class RoutingBridge:
    """Bridges routing decisions with the goal/pipeline system.

    Parameters:
        router: The task router to use for agent selection.
    """

    def __init__(self, router: TaskRouter) -> None:
        self._router = router

    def route_and_assign(
        self,
        task: AgentTask,
        goal_manager: Any,
        pipeline_orchestrator: Any,
    ) -> RouteDecision:
        """Route a task and, if an agent is selected, kick off the pipeline.

        Steps:
        1. Route the task to an agent.
        2. If an agent is selected, create an IntentDeclaration and run
           the pipeline.
        3. If fallback was used, note it in intent metadata.
        4. If MANUAL or no agent found, mark task as needing human assignment.

        Returns:
            The routing decision.
        """
        decision = self._router.route(task)

        if decision.selected_agent_id is not None:
            # Build an intent declaration from the task.
            metadata: dict[str, Any] = {
                "routed_from_task": str(task.task_id),
                "match_score": decision.match_score,
            }
            if decision.fallback_used:
                metadata["fallback_used"] = True
                metadata["note"] = (
                    "No specialist available; routed to generic fallback agent"
                )

            intent = IntentDeclaration(
                agent_id=decision.selected_agent_id,
                description=task.description,
                rationale=f"Auto-routed from task: {task.title}",
                target_files=list(task.target_files),
                target_services=list(task.target_services),
                metadata=metadata,
            )

            # Mark task as assigned.
            goal_manager.update_task_status(task.task_id, TaskStatus.ASSIGNED)

            # Kick off the pipeline.
            pipeline_orchestrator.run(intent, decision.selected_agent_id)
        else:
            # No agent selected — needs human assignment.
            # We leave the task in PENDING status for manual handling.
            pass

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
