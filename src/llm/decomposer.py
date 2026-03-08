"""LLM-powered goal decomposition into agent-sized tasks."""

from __future__ import annotations

import json
import logging
import uuid

from src.goals.decomposer import GoalDecomposer
from src.goals.models import AgentTask, Goal, TaskBreakdown
from src.intent.schema import RiskLevel
from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a task planner for an AI-native CI/CD system. Your job is to decompose
a high-level goal into concrete, agent-executable tasks.

Each task should be a discrete unit of work that one agent can complete. Tasks
can have dependencies on other tasks (by index in the list).

Available risk levels: low, medium, high, critical.

Respond with a JSON object (no markdown fences) matching this schema:
{
  "tasks": [
    {
      "title": "string",
      "description": "string",
      "target_files": ["string"],
      "target_services": ["string"],
      "constraints": ["string"],
      "depends_on_indices": [0],
      "estimated_risk": "low"
    }
  ]
}

Guidelines:
- Always include at least an implementation task and a testing task.
- The testing task should depend on the implementation task.
- Add documentation tasks if the goal mentions docs/documentation.
- Set risk levels appropriately: auth/security/credentials -> critical,
  database/migration/infra/deploy -> high, multi-service -> medium.
- Keep task titles concise and prefixed with their type (Implement, Test, Document, etc.).
"""


class LLMGoalDecomposer:
    """Uses LLM to break a goal into concrete agent tasks."""

    def __init__(self, client: LLMClient | None = None):
        self._client = client or LLMClient()
        self._fallback = GoalDecomposer()

    def decompose(self, goal: Goal) -> TaskBreakdown:
        """Break a goal into tasks using LLM reasoning.

        Falls back to the rule-based GoalDecomposer if the LLM call fails.
        """
        try:
            return self._decompose_with_llm(goal)
        except Exception:
            logger.warning(
                "LLM decomposition failed for goal %s, falling back to rule-based",
                goal.goal_id,
                exc_info=True,
            )
            return self._fallback.decompose(goal)

    def _decompose_with_llm(self, goal: Goal) -> TaskBreakdown:
        """Call LLM and parse the response into a TaskBreakdown."""
        user_prompt = self._build_user_prompt(goal)
        raw = self._client.complete(_SYSTEM_PROMPT, user_prompt)

        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        data = json.loads(text)
        return self._parse_response(goal, data)

    def _build_user_prompt(self, goal: Goal) -> str:
        parts = [
            f"Goal title: {goal.title}",
            f"Description: {goal.description}",
        ]
        if goal.constraints:
            parts.append(f"Constraints: {', '.join(goal.constraints)}")
        if goal.acceptance_criteria:
            parts.append(
                f"Acceptance criteria: {', '.join(goal.acceptance_criteria)}"
            )
        if goal.target_services:
            parts.append(f"Target services: {', '.join(goal.target_services)}")
        if goal.target_paths:
            parts.append(f"Target paths: {', '.join(goal.target_paths)}")
        parts.append(f"Priority: {goal.priority.value}")
        return "\n".join(parts)

    def _parse_response(self, goal: Goal, data: dict) -> TaskBreakdown:
        """Convert parsed JSON into a TaskBreakdown with proper AgentTask objects."""
        raw_tasks = data.get("tasks", [])
        if not raw_tasks:
            raise ValueError("LLM returned no tasks")

        # First pass: create tasks with temporary IDs
        tasks: list[AgentTask] = []
        task_ids: list[uuid.UUID] = []

        for item in raw_tasks:
            risk_str = item.get("estimated_risk", "low").lower()
            try:
                risk = RiskLevel(risk_str)
            except ValueError:
                risk = RiskLevel.LOW

            task = AgentTask(
                goal_id=goal.goal_id,
                title=item.get("title", "Untitled task"),
                description=item.get("description", ""),
                target_files=item.get("target_files", []),
                target_services=item.get("target_services", []),
                constraints=item.get("constraints", []),
                depends_on=[],
                estimated_risk=risk,
                status="pending",
            )
            tasks.append(task)
            task_ids.append(task.task_id)

        # Second pass: resolve dependency indices to UUIDs
        for i, item in enumerate(raw_tasks):
            dep_indices = item.get("depends_on_indices", [])
            for idx in dep_indices:
                if 0 <= idx < len(task_ids) and idx != i:
                    tasks[i].depends_on.append(task_ids[idx])

        return TaskBreakdown(goal_id=goal.goal_id, tasks=tasks)
