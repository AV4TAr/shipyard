"""Rule-based project planning — breaks a project into milestones.

This module generates a milestone structure for a project based on
simple heuristics. A future version will delegate to an LLM planner
for richer, context-aware milestone generation.

# TODO: Replace rule-based heuristics with an LLM-powered planner.
"""

from __future__ import annotations

from .models import Milestone, Project


class ProjectPlanner:
    """Creates milestone structure for a project.

    Currently uses rule-based logic. Will integrate with LLM later.
    """

    def plan(self, project: Project) -> list[Milestone]:
        """Generate milestones for a project.

        Default strategy: 3 milestones
        1. Foundation — setup, infrastructure, base architecture
        2. Implementation — core features, business logic
        3. Polish — testing, docs, hardening
        """
        milestones: list[Milestone] = []

        # Milestone 1: Foundation
        foundation_criteria = [
            "Base project structure is in place",
            "Infrastructure dependencies are configured",
        ]
        if project.target_services:
            foundation_criteria.append(
                f"Service scaffolding for: {', '.join(project.target_services)}"
            )

        milestones.append(
            Milestone(
                title="Foundation",
                description=(
                    "Setup, infrastructure, and base architecture for: "
                    f"{project.title}"
                ),
                order=0,
                acceptance_criteria=foundation_criteria,
            )
        )

        # Milestone 2: Implementation
        impl_criteria = ["Core features are implemented and functional"]
        if project.constraints:
            impl_criteria.append("All project constraints are respected")

        milestones.append(
            Milestone(
                title="Implementation",
                description=(
                    f"Core features and business logic for: {project.title}"
                ),
                order=1,
                acceptance_criteria=impl_criteria,
            )
        )

        # Milestone 3: Polish
        milestones.append(
            Milestone(
                title="Polish",
                description=(
                    f"Testing, documentation, and hardening for: {project.title}"
                ),
                order=2,
                acceptance_criteria=[
                    "All tests pass with adequate coverage",
                    "Documentation is complete and up to date",
                    "No known critical issues remain",
                ],
            )
        )

        return milestones
