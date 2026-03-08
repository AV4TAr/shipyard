"""Rule-based goal decomposition into agent-sized tasks.

This module breaks a high-level Goal into a list of AgentTasks that agents
can execute independently (respecting declared dependencies).

# TODO: Replace rule-based heuristics with an LLM-powered planner agent.
#       The planner would receive the Goal, the current codebase context,
#       and produce a smarter, context-aware TaskBreakdown.
"""

from __future__ import annotations

import re
import uuid

from src.intent.schema import RiskLevel

from .models import AgentTask, Goal, TaskBreakdown


# Keywords used to detect whether documentation tasks are needed.
_DOC_KEYWORDS: set[str] = {
    "document",
    "documentation",
    "readme",
    "docs",
    "docstring",
    "api docs",
    "wiki",
}

# Keywords signalling that testing work is explicitly requested.
_TEST_KEYWORDS: set[str] = {
    "test",
    "tests",
    "testing",
    "coverage",
    "unit test",
    "integration test",
    "e2e",
}

# Patterns that suggest higher-risk work.
_HIGH_RISK_PATTERNS: list[str] = [
    r"auth",
    r"security",
    r"secret",
    r"credential",
    r"password",
    r"deploy",
    r"migration",
    r"database",
    r"infra",
    r"production",
]


class GoalDecomposer:
    """Decomposes a :class:`Goal` into a :class:`TaskBreakdown`.

    Currently uses rule-based heuristics.  A future version will delegate
    to a planner agent (LLM) for richer, context-aware decomposition.

    # TODO: Accept a codebase index / context so the planner can make
    #       smarter decisions about which files and services are involved.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decompose(self, goal: Goal) -> TaskBreakdown:
        """Break *goal* into a list of :class:`AgentTask` items.

        Strategy (rule-based):
        1. Always create an **implementation** task.
        2. Always create a **testing** task (depends on implementation).
        3. If the description mentions documentation keywords, create a
           **documentation** task (depends on implementation).

        All tasks inherit the goal's constraints.
        """
        tasks: list[AgentTask] = []
        description_lower = goal.description.lower()

        # --- 1. Implementation task (always present) ---
        impl_task = AgentTask(
            goal_id=goal.goal_id,
            title=f"Implement: {goal.title}",
            description=f"Implement the changes described in the goal: {goal.description}",
            target_files=list(goal.target_paths),
            target_services=list(goal.target_services),
            constraints=list(goal.constraints),
            depends_on=[],
            estimated_risk=self.estimate_risk_from_goal(goal),
            status="pending",
        )
        tasks.append(impl_task)

        # --- 2. Testing task (always present, depends on implementation) ---
        test_task = AgentTask(
            goal_id=goal.goal_id,
            title=f"Test: {goal.title}",
            description=f"Write and run tests for: {goal.description}",
            target_files=[],
            target_services=list(goal.target_services),
            constraints=list(goal.constraints) + [
                "All new code must have test coverage",
            ],
            depends_on=[impl_task.task_id],
            estimated_risk=RiskLevel.LOW,
            status="pending",
        )
        tasks.append(test_task)

        # --- 3. Documentation task (conditional) ---
        if self._needs_documentation(description_lower):
            doc_task = AgentTask(
                goal_id=goal.goal_id,
                title=f"Document: {goal.title}",
                description=f"Update documentation for: {goal.description}",
                target_files=[],
                target_services=[],
                constraints=list(goal.constraints),
                depends_on=[impl_task.task_id],
                estimated_risk=RiskLevel.LOW,
                status="pending",
            )
            tasks.append(doc_task)

        # Estimate risk for each task individually
        for task in tasks:
            task.estimated_risk = self.estimate_risk(task)

        return TaskBreakdown(goal_id=goal.goal_id, tasks=tasks)

    def estimate_risk(self, task: AgentTask) -> RiskLevel:
        """Estimate the risk of a single task based on its target files/services.

        Heuristics:
        - Touching auth / security / credentials -> CRITICAL
        - Touching database / migration / infra -> HIGH
        - Touching deploy / production -> HIGH
        - Multiple services affected -> MEDIUM
        - Otherwise -> LOW
        """
        combined = " ".join(
            task.target_files + task.target_services + [task.description]
        ).lower()

        # Check for critical patterns
        critical_patterns = {"auth", "security", "secret", "credential", "password"}
        for pattern in critical_patterns:
            if re.search(pattern, combined):
                return RiskLevel.CRITICAL

        # Check for high-risk patterns
        high_patterns = {"database", "migration", "infra", "production", "deploy"}
        for pattern in high_patterns:
            if re.search(pattern, combined):
                return RiskLevel.HIGH

        # Multiple services -> medium risk
        if len(task.target_services) > 1:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def estimate_risk_from_goal(self, goal: Goal) -> RiskLevel:
        """Quick risk estimate from goal-level information."""
        combined = " ".join(
            [goal.description, goal.title] + goal.target_services + goal.target_paths
        ).lower()

        for pattern in _HIGH_RISK_PATTERNS:
            if re.search(pattern, combined):
                return RiskLevel.HIGH

        if len(goal.target_services) > 1:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    @staticmethod
    def _needs_documentation(description_lower: str) -> bool:
        """Return True if the goal description suggests docs work."""
        return any(kw in description_lower for kw in _DOC_KEYWORDS)
