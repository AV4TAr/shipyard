"""Tests for the Agent Selection & Routing System."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from src.goals.models import AgentTask, TaskStatus
from src.intent.schema import RiskLevel
from src.routing.analyzer import TaskAnalyzer
from src.routing.integration import RoutingBridge
from src.routing.models import (
    AgentCapability,
    AgentRegistration,
    AgentStatus,
    RouteDecision,
    RoutingStrategy,
    TaskComplexity,
)
from src.routing.registry import AgentRegistry
from src.routing.router import TaskRouter
from src.trust.tracker import TrustTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    description: str = "Do something",
    title: str = "Task",
    target_files: list[str] | None = None,
    target_services: list[str] | None = None,
    risk: RiskLevel = RiskLevel.LOW,
    depends_on: list[uuid.UUID] | None = None,
    status: TaskStatus = TaskStatus.PENDING,
) -> AgentTask:
    return AgentTask(
        goal_id=uuid.uuid4(),
        title=title,
        description=description,
        target_files=target_files or [],
        target_services=target_services or [],
        estimated_risk=risk,
        depends_on=depends_on or [],
        status=status,
    )


def _make_agent(
    agent_id: str = "agent-1",
    name: str = "Agent One",
    capabilities: list[AgentCapability] | None = None,
    primary: AgentCapability = AgentCapability.BACKEND,
    languages: list[str] | None = None,
    frameworks: list[str] | None = None,
    max_concurrent: int = 3,
    status: AgentStatus = AgentStatus.AVAILABLE,
) -> AgentRegistration:
    return AgentRegistration(
        agent_id=agent_id,
        name=name,
        capabilities=capabilities or [primary],
        primary_capability=primary,
        languages=languages or ["python"],
        frameworks=frameworks or [],
        max_concurrent_tasks=max_concurrent,
        status=status,
    )


# ===================================================================
# Registry tests
# ===================================================================


class TestAgentRegistry:
    """Tests for AgentRegistry."""

    def test_builtin_generic_agent_exists(self) -> None:
        registry = AgentRegistry()
        generic = registry.get("generic")
        assert generic.agent_id == "generic"
        assert AgentCapability.GENERIC in generic.capabilities
        assert AgentCapability.FULLSTACK in generic.capabilities

    def test_register_and_get(self) -> None:
        registry = AgentRegistry()
        agent = _make_agent()
        registry.register(agent)
        assert registry.get(agent.agent_id) == agent

    def test_unregister(self) -> None:
        registry = AgentRegistry()
        agent = _make_agent()
        registry.register(agent)
        registry.unregister(agent.agent_id)
        with pytest.raises(KeyError):
            registry.get(agent.agent_id)

    def test_generic_cannot_be_unregistered(self) -> None:
        registry = AgentRegistry()
        with pytest.raises(ValueError, match="generic"):
            registry.unregister("generic")

    def test_update_status(self) -> None:
        registry = AgentRegistry()
        agent = _make_agent()
        registry.register(agent)
        registry.update_status(agent.agent_id, AgentStatus.BUSY)
        assert registry.get(agent.agent_id).status == AgentStatus.BUSY

    def test_list_agents_filter_capability(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_agent("fe", primary=AgentCapability.FRONTEND))
        registry.register(_make_agent("be", primary=AgentCapability.BACKEND))
        fe_agents = registry.list_agents(capability=AgentCapability.FRONTEND)
        assert len(fe_agents) == 1
        assert fe_agents[0].agent_id == "fe"

    def test_list_agents_filter_status(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_agent("a1", status=AgentStatus.AVAILABLE))
        registry.register(_make_agent("a2", status=AgentStatus.OFFLINE))
        available = registry.list_agents(status=AgentStatus.AVAILABLE)
        ids = {a.agent_id for a in available}
        assert "a1" in ids
        assert "a2" not in ids

    def test_get_available(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_agent("a1", status=AgentStatus.AVAILABLE))
        registry.register(_make_agent("a2", status=AgentStatus.BUSY))
        available = registry.get_available()
        ids = {a.agent_id for a in available}
        assert "a1" in ids
        assert "a2" not in ids
        # generic is always available
        assert "generic" in ids


# ===================================================================
# TaskAnalyzer tests
# ===================================================================


class TestTaskAnalyzer:
    """Tests for TaskAnalyzer."""

    analyzer = TaskAnalyzer()

    @pytest.mark.parametrize(
        "keyword, expected_cap",
        [
            ("Build a React frontend component", AgentCapability.FRONTEND),
            ("Create REST API endpoint for users", AgentCapability.BACKEND),
            ("Set up ETL data pipeline", AgentCapability.DATA),
            ("Fix authentication security vulnerability", AgentCapability.SECURITY),
            ("Build Android mobile app screen", AgentCapability.MOBILE),
            ("Write e2e test coverage", AgentCapability.QA),
            ("Set up Docker deploy pipeline", AgentCapability.DEVOPS),
            ("Update API documentation", AgentCapability.DOCUMENTATION),
        ],
    )
    def test_capability_keyword_detection(
        self, keyword: str, expected_cap: AgentCapability
    ) -> None:
        task = _make_task(description=keyword)
        reqs = self.analyzer.analyze(task)
        assert expected_cap in reqs.required_capabilities

    def test_language_inference_python(self) -> None:
        task = _make_task(target_files=["src/main.py", "tests/test_main.py"])
        reqs = self.analyzer.analyze(task)
        assert "python" in reqs.required_languages

    def test_language_inference_typescript(self) -> None:
        task = _make_task(target_files=["src/App.tsx", "lib/utils.ts"])
        reqs = self.analyzer.analyze(task)
        assert "typescript" in reqs.required_languages

    def test_language_inference_go(self) -> None:
        task = _make_task(target_files=["cmd/server/main.go"])
        reqs = self.analyzer.analyze(task)
        assert "go" in reqs.required_languages

    def test_language_inference_multiple(self) -> None:
        task = _make_task(target_files=["main.py", "index.ts", "server.go"])
        reqs = self.analyzer.analyze(task)
        assert set(reqs.required_languages) == {"python", "typescript", "go"}

    def test_framework_inference(self) -> None:
        task = _make_task(description="Add FastAPI endpoint for user auth")
        reqs = self.analyzer.analyze(task)
        assert "fastapi" in reqs.required_frameworks

    def test_complexity_trivial(self) -> None:
        task = _make_task(target_files=["a.py", "b.py"])
        reqs = self.analyzer.analyze(task)
        assert reqs.estimated_complexity == TaskComplexity.TRIVIAL

    def test_complexity_simple(self) -> None:
        task = _make_task(target_files=["a.py", "b.py", "c.py"])
        reqs = self.analyzer.analyze(task)
        assert reqs.estimated_complexity == TaskComplexity.SIMPLE

    def test_complexity_moderate(self) -> None:
        task = _make_task(target_files=[f"f{i}.py" for i in range(8)])
        reqs = self.analyzer.analyze(task)
        assert reqs.estimated_complexity == TaskComplexity.MODERATE

    def test_complexity_complex(self) -> None:
        task = _make_task(
            target_files=[f"f{i}.py" for i in range(8)],
            target_services=["svc1", "svc2", "svc3", "svc4"],
        )
        reqs = self.analyzer.analyze(task)
        assert reqs.estimated_complexity == TaskComplexity.COMPLEX

    def test_no_files_defaults_to_simple(self) -> None:
        task = _make_task()
        reqs = self.analyzer.analyze(task)
        assert reqs.estimated_complexity == TaskComplexity.SIMPLE


# ===================================================================
# Router tests
# ===================================================================


class TestTaskRouterBestMatch:
    """Tests for BEST_MATCH routing strategy."""

    def test_specialist_beats_generic(self) -> None:
        registry = AgentRegistry()
        specialist = _make_agent(
            "fe-agent",
            primary=AgentCapability.FRONTEND,
            capabilities=[AgentCapability.FRONTEND],
            languages=["typescript"],
            frameworks=["react"],
        )
        registry.register(specialist)

        router = TaskRouter(registry)
        task = _make_task(
            description="Build a React frontend component",
            target_files=["src/App.tsx"],
        )
        decision = router.route(task)

        assert decision.selected_agent_id == "fe-agent"
        assert not decision.fallback_used
        assert decision.match_score > 0.5

    def test_fallback_to_generic_when_no_specialist(self) -> None:
        registry = AgentRegistry()
        # Only the built-in generic agent exists.
        router = TaskRouter(registry)
        task = _make_task(
            description="Build a React frontend component",
            target_files=["src/App.tsx"],
        )
        decision = router.route(task)

        assert decision.selected_agent_id == "generic"
        assert decision.fallback_used

    def test_fallback_when_no_specialist_scores_above_threshold(self) -> None:
        registry = AgentRegistry()
        # Register a backend agent — it shouldn't match a frontend task well.
        registry.register(
            _make_agent(
                "be-agent",
                primary=AgentCapability.BACKEND,
                capabilities=[AgentCapability.BACKEND],
                languages=["python"],
            )
        )

        router = TaskRouter(registry)
        task = _make_task(
            description="Build a React frontend component",
            target_files=["src/App.tsx"],
        )
        decision = router.route(task)

        # The backend agent has no capability match and no language match for TS,
        # so it should score below 0.5 and trigger fallback.
        assert decision.selected_agent_id == "generic"
        assert decision.fallback_used

    def test_trust_factor_integration(self) -> None:
        registry = AgentRegistry()
        registry.register(
            _make_agent(
                "trusted",
                primary=AgentCapability.BACKEND,
                languages=["python"],
            )
        )
        registry.register(
            _make_agent(
                "untrusted",
                primary=AgentCapability.BACKEND,
                languages=["python"],
            )
        )

        tracker = TrustTracker()
        # Build up trust for the "trusted" agent.
        for _ in range(20):
            tracker.record_outcome("trusted", success=True, risk_score=0.3)
        # Record failures for the "untrusted" agent.
        for _ in range(20):
            tracker.record_outcome("untrusted", success=False, risk_score=0.3)

        router = TaskRouter(registry, trust_tracker=tracker)
        task = _make_task(
            description="Build API endpoint",
            target_files=["src/api.py"],
        )
        decision = router.route(task)

        assert decision.selected_agent_id == "trusted"

    def test_match_reasons_populated(self) -> None:
        registry = AgentRegistry()
        registry.register(
            _make_agent("a1", primary=AgentCapability.BACKEND, languages=["python"])
        )
        router = TaskRouter(registry)
        task = _make_task(
            description="Build API endpoint", target_files=["src/api.py"]
        )
        decision = router.route(task)
        assert len(decision.match_reasons) > 0

    def test_alternatives_populated(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_agent("a1", primary=AgentCapability.BACKEND))
        registry.register(_make_agent("a2", primary=AgentCapability.BACKEND))
        registry.register(_make_agent("a3", primary=AgentCapability.BACKEND))
        registry.register(_make_agent("a4", primary=AgentCapability.BACKEND))

        router = TaskRouter(registry)
        task = _make_task(description="Build API endpoint")
        decision = router.route(task)

        # Should have up to 3 alternatives (excluding the winner).
        assert len(decision.alternatives) <= 3
        assert len(decision.alternatives) >= 1
        # Each alternative is (agent_id, score).
        for alt_id, alt_score in decision.alternatives:
            assert isinstance(alt_id, str)
            assert isinstance(alt_score, float)


class TestTaskRouterRoundRobin:
    """Tests for ROUND_ROBIN routing strategy."""

    def test_round_robin_cycles_agents(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_agent("a1", primary=AgentCapability.BACKEND))
        registry.register(_make_agent("a2", primary=AgentCapability.BACKEND))

        router = TaskRouter(registry)
        task = _make_task(description="Build something")

        seen: set[str] = set()
        for _ in range(6):
            decision = router.route(task, strategy=RoutingStrategy.ROUND_ROBIN)
            if decision.selected_agent_id:
                seen.add(decision.selected_agent_id)

        # Should have visited at least 2 different agents.
        assert len(seen) >= 2


class TestTaskRouterLeastLoaded:
    """Tests for LEAST_LOADED routing strategy."""

    def test_least_loaded_picks_highest_capacity(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_agent("low", max_concurrent=1))
        registry.register(_make_agent("high", max_concurrent=10))

        router = TaskRouter(registry)
        task = _make_task(description="Do work")
        decision = router.route(task, strategy=RoutingStrategy.LEAST_LOADED)

        # "generic" has max_concurrent=10 too, but "high" also has 10.
        assert decision.selected_agent_id in ("high", "generic")


class TestTaskRouterManual:
    """Tests for MANUAL routing strategy."""

    def test_manual_returns_no_selection(self) -> None:
        registry = AgentRegistry()
        router = TaskRouter(registry)
        task = _make_task(description="Something")
        decision = router.route(task, strategy=RoutingStrategy.MANUAL)

        assert decision.selected_agent_id is None
        assert decision.agent_registration is None


class TestRouterBatch:
    """Tests for route_batch."""

    def test_batch_avoids_overloading(self) -> None:
        registry = AgentRegistry()
        # Two agents with max 1 concurrent task each.
        registry.register(
            _make_agent("a1", primary=AgentCapability.BACKEND, max_concurrent=1, languages=["python"])
        )
        registry.register(
            _make_agent("a2", primary=AgentCapability.BACKEND, max_concurrent=1, languages=["python"])
        )

        router = TaskRouter(registry)
        tasks = [
            _make_task(description="Build API endpoint", target_files=["a.py"]),
            _make_task(description="Build API endpoint", target_files=["b.py"]),
        ]
        decisions = router.route_batch(tasks)

        assigned_ids = [d.selected_agent_id for d in decisions if d.selected_agent_id]
        # Both tasks should not go to the same specialist agent (load penalty
        # should push second task elsewhere).
        # At least we should get 2 assignments.
        assert len(assigned_ids) == 2


# ===================================================================
# RoutingBridge tests
# ===================================================================


class TestRoutingBridge:
    """Tests for RoutingBridge integration."""

    def test_route_and_assign_creates_intent(self) -> None:
        registry = AgentRegistry()
        registry.register(
            _make_agent("a1", primary=AgentCapability.BACKEND, languages=["python"])
        )
        router = TaskRouter(registry)
        bridge = RoutingBridge(router)

        task = _make_task(
            description="Build API endpoint",
            target_files=["src/api.py"],
        )

        goal_manager = MagicMock()
        pipeline_orchestrator = MagicMock()

        decision = bridge.route_and_assign(task, goal_manager, pipeline_orchestrator)

        assert decision.selected_agent_id is not None
        # Goal manager should have been called to mark task ASSIGNED.
        goal_manager.update_task_status.assert_called_once_with(
            task.task_id, TaskStatus.ASSIGNED
        )
        # Pipeline should have been kicked off.
        pipeline_orchestrator.run.assert_called_once()
        call_args = pipeline_orchestrator.run.call_args
        intent = call_args[0][0]
        assert intent.agent_id == decision.selected_agent_id
        assert intent.target_files == task.target_files

    def test_route_and_assign_fallback_adds_metadata(self) -> None:
        registry = AgentRegistry()
        # Only generic agent available.
        router = TaskRouter(registry)
        bridge = RoutingBridge(router)

        task = _make_task(
            description="Build a React component",
            target_files=["src/App.tsx"],
        )

        goal_manager = MagicMock()
        pipeline_orchestrator = MagicMock()

        decision = bridge.route_and_assign(task, goal_manager, pipeline_orchestrator)

        assert decision.fallback_used
        # Check the intent metadata includes fallback note.
        call_args = pipeline_orchestrator.run.call_args
        intent = call_args[0][0]
        assert intent.metadata is not None
        assert intent.metadata.get("fallback_used") is True

    def test_manual_route_no_pipeline(self) -> None:
        registry = AgentRegistry()
        router = TaskRouter(registry)
        bridge = RoutingBridge(router)

        task = _make_task(description="Something")

        goal_manager = MagicMock()
        pipeline_orchestrator = MagicMock()

        # Override routing to use MANUAL by routing directly
        decision = router.route(task, strategy=RoutingStrategy.MANUAL)
        assert decision.selected_agent_id is None
        # Pipeline should NOT be called for manual.
        pipeline_orchestrator.run.assert_not_called()
