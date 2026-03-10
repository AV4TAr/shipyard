"""TaskRouter — selects the best agent for a given task."""

from __future__ import annotations

from src.goals.models import AgentTask
from src.trust.tracker import TrustTracker

from .analyzer import TaskAnalyzer
from .models import (
    AgentCapability,
    AgentRegistration,
    AgentStatus,
    RouteDecision,
    RoutingStrategy,
    TaskRequirements,
)
from .registry import AgentRegistry


class TaskRouter:
    """Routes tasks to agents using pluggable strategies.

    Parameters:
        registry: The agent registry to draw candidates from.
        trust_tracker: Optional trust tracker for trust-weighted scoring.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        trust_tracker: TrustTracker | None = None,
    ) -> None:
        self._registry = registry
        self._trust_tracker = trust_tracker
        self._analyzer = TaskAnalyzer()
        self._round_robin_index: int = 0
        # Tracks how many tasks have been assigned to each agent in a batch.
        self._batch_load: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(
        self,
        task: AgentTask,
        strategy: RoutingStrategy = RoutingStrategy.BEST_MATCH,
    ) -> RouteDecision:
        """Select the best agent for *task* according to *strategy*."""
        task_id = str(task.task_id)

        if strategy == RoutingStrategy.MANUAL:
            return RouteDecision(
                task_id=task_id,
                match_reasons=["Manual assignment requested — no automatic selection"],
            )

        requirements = self._analyzer.analyze(task)

        if strategy == RoutingStrategy.BEST_MATCH:
            return self._route_best_match(task_id, requirements)
        if strategy == RoutingStrategy.ROUND_ROBIN:
            return self._route_round_robin(task_id, requirements)
        if strategy == RoutingStrategy.LEAST_LOADED:
            return self._route_least_loaded(task_id, requirements)

        # Fallback (should not happen with the enum)
        return RouteDecision(task_id=task_id)

    def route_batch(self, tasks: list[AgentTask]) -> list[RouteDecision]:
        """Route multiple tasks, avoiding overloading a single agent.

        Uses BEST_MATCH strategy with incremental load tracking so that
        subsequent tasks consider previous assignments in this batch.
        """
        self._batch_load.clear()
        decisions: list[RouteDecision] = []

        for task in tasks:
            requirements = self._analyzer.analyze(task)
            decision = self._route_best_match(
                str(task.task_id), requirements, batch_mode=True
            )
            if decision.selected_agent_id:
                self._batch_load[decision.selected_agent_id] = (
                    self._batch_load.get(decision.selected_agent_id, 0) + 1
                )
            decisions.append(decision)

        self._batch_load.clear()
        return decisions

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _route_best_match(
        self,
        task_id: str,
        requirements: TaskRequirements,
        *,
        batch_mode: bool = False,
    ) -> RouteDecision:
        available = self._registry.get_available()
        if not available:
            return RouteDecision(task_id=task_id, match_reasons=["No agents available"])

        scored: list[tuple[AgentRegistration, float, list[str]]] = []
        for agent in available:
            score, reasons = self._score_agent(agent, requirements, batch_mode=batch_mode)
            scored.append((agent, score, reasons))

        scored.sort(key=lambda t: t[1], reverse=True)

        best_agent, best_score, best_reasons = scored[0]
        fallback_used = False

        # If no specialist scores above 0.5, fall back to generic.
        if best_score < 0.5:
            generic = self._registry.get("generic")
            generic_score, generic_reasons = self._score_agent(
                generic, requirements, batch_mode=batch_mode
            )
            best_agent = generic
            best_score = generic_score
            best_reasons = generic_reasons
            fallback_used = True

        # Also flag fallback if the selected agent IS the generic agent.
        if best_agent.agent_id == "generic":
            fallback_used = True

        # Build alternatives (top 3 excluding the winner).
        alternatives: list[tuple[str, float]] = []
        for agent, score, _ in scored:
            if agent.agent_id != best_agent.agent_id:
                alternatives.append((agent.agent_id, round(score, 4)))
            if len(alternatives) >= 3:
                break

        return RouteDecision(
            task_id=task_id,
            selected_agent_id=best_agent.agent_id,
            agent_registration=best_agent,
            match_score=round(best_score, 4),
            match_reasons=best_reasons,
            fallback_used=fallback_used,
            alternatives=alternatives,
        )

    def _route_round_robin(
        self, task_id: str, requirements: TaskRequirements
    ) -> RouteDecision:
        candidates = self._registry.get_available()
        if not candidates:
            return RouteDecision(task_id=task_id, match_reasons=["No agents available"])

        # Filter to agents with at least one matching capability.
        if requirements.required_capabilities:
            matching = [
                a
                for a in candidates
                if any(c in a.capabilities for c in requirements.required_capabilities)
            ]
            if matching:
                candidates = matching

        idx = self._round_robin_index % len(candidates)
        self._round_robin_index += 1
        selected = candidates[idx]

        return RouteDecision(
            task_id=task_id,
            selected_agent_id=selected.agent_id,
            agent_registration=selected,
            match_score=0.5,
            match_reasons=[f"Round-robin selection (index {idx})"],
            fallback_used=selected.agent_id == "generic",
        )

    def _route_least_loaded(
        self, task_id: str, requirements: TaskRequirements
    ) -> RouteDecision:
        candidates = self._registry.get_available()
        if not candidates:
            return RouteDecision(task_id=task_id, match_reasons=["No agents available"])

        # Sort by how much spare capacity they have (descending).
        candidates.sort(key=lambda a: a.max_concurrent_tasks, reverse=True)
        selected = candidates[0]

        return RouteDecision(
            task_id=task_id,
            selected_agent_id=selected.agent_id,
            agent_registration=selected,
            match_score=0.5,
            match_reasons=[
                f"Least-loaded selection (max_concurrent_tasks={selected.max_concurrent_tasks})"
            ],
            fallback_used=selected.agent_id == "generic",
        )

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_agent(
        self,
        agent: AgentRegistration,
        requirements: TaskRequirements,
        *,
        batch_mode: bool = False,
    ) -> tuple[float, list[str]]:
        """Score an agent against task requirements.

        Returns (score, reasons) where score is in [0, 1].
        """
        reasons: list[str] = []

        # --- Capability match (weight 0.35) ---
        cap_score = 0.0
        if requirements.required_capabilities:
            if agent.primary_capability in requirements.required_capabilities:
                cap_score = 1.0
                reasons.append(
                    f"Primary capability {agent.primary_capability.value} matches"
                )
            elif any(c in agent.capabilities for c in requirements.required_capabilities):
                cap_score = 0.7
                reasons.append("Secondary capability match")
            elif AgentCapability.GENERIC in agent.capabilities:
                cap_score = 0.3
                reasons.append("Generic agent fallback capability")
            else:
                cap_score = 0.0
                reasons.append("No capability match")
        else:
            # No specific capabilities required — any agent is fine.
            cap_score = 0.5
            reasons.append("No specific capability required")

        # --- Language match (weight 0.20) ---
        lang_score = 0.0
        if requirements.required_languages:
            agent_langs = {lang.lower() for lang in agent.languages}
            matched = sum(
                1 for rl in requirements.required_languages
                if rl.lower() in agent_langs
            )
            lang_score = matched / len(requirements.required_languages)
            if lang_score > 0:
                reasons.append(
                    f"Language match: {matched}/{len(requirements.required_languages)}"
                )
        else:
            lang_score = 0.5

        # --- Framework match (weight 0.15) ---
        fw_score = 0.0
        if requirements.required_frameworks:
            agent_fws = {f.lower() for f in agent.frameworks}
            matched = sum(1 for f in requirements.required_frameworks if f.lower() in agent_fws)
            fw_score = matched / len(requirements.required_frameworks)
            if fw_score > 0:
                reasons.append(
                    f"Framework match: {matched}/{len(requirements.required_frameworks)}"
                )
        else:
            fw_score = 0.5

        # --- Trust factor (weight 0.20) ---
        trust_score = 0.5
        if self._trust_tracker is not None:
            # Prefer domain-specific trust when the task requires capabilities.
            domain = None
            if requirements.required_capabilities:
                domain = requirements.required_capabilities[0].value
            if domain:
                trust_score = self._trust_tracker.compute_domain_trust(
                    agent.agent_id, domain
                )
                reasons.append(f"Domain trust ({domain}): {trust_score:.2f}")
            else:
                trust_score = self._trust_tracker.compute_trust_score(agent.agent_id)
                reasons.append(f"Trust score: {trust_score:.2f}")

        # --- Load factor (weight 0.10) ---
        load_score = 1.0
        if batch_mode:
            current_load = self._batch_load.get(agent.agent_id, 0)
            if current_load >= agent.max_concurrent_tasks:
                load_score = 0.0
                reasons.append("Agent at max capacity (batch)")
            else:
                load_score = 1.0 - (current_load / agent.max_concurrent_tasks)
        if agent.status != AgentStatus.AVAILABLE:
            load_score = 0.0

        # --- Weighted sum ---
        score = (
            cap_score * 0.35
            + lang_score * 0.20
            + fw_score * 0.15
            + trust_score * 0.20
            + load_score * 0.10
        )

        return score, reasons
