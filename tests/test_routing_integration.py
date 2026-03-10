"""Integration tests for the routing system wired into the runtime."""

from __future__ import annotations

import pytest

from src.cli.runtime import CLIRuntime, _capability_from_string
from src.routing.models import (
    AgentCapability,
    AgentRegistration,
    RouteDecision,
)
from src.routing.registry import AgentRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runtime() -> CLIRuntime:
    """Create a CLIRuntime via from_defaults() with in-memory storage."""
    return CLIRuntime.from_defaults()


# ---------------------------------------------------------------------------
# Task 1: CLIRuntime has routing components
# ---------------------------------------------------------------------------


class TestRuntimeRoutingComponents:
    def test_from_defaults_creates_routing_components(self, runtime: CLIRuntime):
        assert runtime.agent_registry is not None
        assert runtime.task_router is not None
        assert runtime.routing_bridge is not None

    def test_agent_registry_has_generic_agent(self, runtime: CLIRuntime):
        assert runtime.agent_registry is not None
        generic = runtime.agent_registry.get("generic")
        assert generic.agent_id == "generic"
        assert AgentCapability.GENERIC in generic.capabilities

    def test_register_agent_via_runtime(self, runtime: CLIRuntime):
        reg = runtime.register_agent(
            agent_id="test-agent",
            name="Test Agent",
            capabilities=["frontend", "backend"],
        )
        assert reg.agent_id == "test-agent"
        assert AgentCapability.FRONTEND in reg.capabilities
        assert AgentCapability.BACKEND in reg.capabilities

    def test_list_registered_agents(self, runtime: CLIRuntime):
        runtime.register_agent(
            agent_id="agent-a",
            name="Agent A",
            capabilities=["security"],
        )
        agents = runtime.list_registered_agents()
        ids = {a.agent_id for a in agents}
        assert "agent-a" in ids
        assert "generic" in ids  # always present


# ---------------------------------------------------------------------------
# Task 2: SDK bridge into routing
# ---------------------------------------------------------------------------


class TestSDKBridge:
    def test_sdk_registration_bridges_to_routing(self, runtime: CLIRuntime):
        """Simulating what the SDK /register endpoint does."""
        # This mirrors what src/sdk/routes.py does.
        runtime.register_agent(
            agent_id="sdk-agent",
            name="SDK Agent",
            capabilities=["frontend", "qa"],
            languages=["typescript"],
            frameworks=["react"],
            max_concurrent_tasks=3,
        )

        assert runtime.agent_registry is not None
        agent = runtime.agent_registry.get("sdk-agent")
        assert agent.name == "SDK Agent"
        assert AgentCapability.FRONTEND in agent.capabilities
        assert AgentCapability.QA in agent.capabilities
        assert "typescript" in agent.languages
        assert "react" in agent.frameworks
        assert agent.max_concurrent_tasks == 3

    def test_unknown_capability_maps_to_generic(self):
        cap = _capability_from_string("unknown_thing")
        assert cap == AgentCapability.GENERIC

    def test_known_capability_maps_correctly(self):
        assert _capability_from_string("security") == AgentCapability.SECURITY
        assert _capability_from_string("FRONTEND") == AgentCapability.FRONTEND


# ---------------------------------------------------------------------------
# Task 3: Domain-specific trust
# ---------------------------------------------------------------------------


class TestDomainTrust:
    def test_domain_scores_default_empty(self, runtime: CLIRuntime):
        profile = runtime.trust_tracker.get_profile("new-agent")
        assert profile.domain_scores == {}

    def test_record_outcome_with_domain(self, runtime: CLIRuntime):
        runtime.trust_tracker.record_outcome(
            "agent-x", success=True, risk_score=0.3, domain="security"
        )
        profile = runtime.trust_tracker.get_profile("agent-x")
        assert "security" in profile.domain_scores
        assert profile.domain_scores["security"] > 0.5

    def test_compute_domain_trust_with_history(self, runtime: CLIRuntime):
        # Record several successful security deployments.
        for _ in range(5):
            runtime.trust_tracker.record_outcome(
                "sec-agent", success=True, risk_score=0.2, domain="security"
            )
        domain_score = runtime.trust_tracker.compute_domain_trust(
            "sec-agent", "security"
        )
        # Domain score should be high due to repeated successes.
        assert domain_score > 0.7

    def test_compute_domain_trust_fallback_to_general(self, runtime: CLIRuntime):
        """When no domain history exists, falls back to general trust."""
        runtime.trust_tracker.record_outcome(
            "gen-agent", success=True, risk_score=0.3
        )
        domain_score = runtime.trust_tracker.compute_domain_trust(
            "gen-agent", "frontend"
        )
        general_score = runtime.trust_tracker.compute_trust_score("gen-agent")
        assert domain_score == general_score

    def test_router_prefers_domain_trust(self, runtime: CLIRuntime):
        """An agent trusted for 'security' should score higher on security tasks."""
        assert runtime.agent_registry is not None
        assert runtime.task_router is not None

        # Register two agents.
        runtime.register_agent(
            agent_id="sec-specialist",
            name="Security Specialist",
            capabilities=["security"],
        )
        runtime.register_agent(
            agent_id="gen-dev",
            name="General Dev",
            capabilities=["security"],
        )

        # Build domain trust for the specialist.
        for _ in range(10):
            runtime.trust_tracker.record_outcome(
                "sec-specialist",
                success=True,
                risk_score=0.2,
                domain="security",
            )
        # General dev has no domain trust.
        for _ in range(10):
            runtime.trust_tracker.record_outcome(
                "gen-dev",
                success=True,
                risk_score=0.2,
            )

        # Create a goal with a security task.
        goal = runtime.create_goal(
            title="Security audit",
            description="Run security scan on the auth module",
            priority="high",
        )
        runtime.activate_goal(str(goal.goal_id))
        tasks = runtime.goal_manager.get_tasks(goal.goal_id)
        assert len(tasks) > 0

        # Route and check who gets selected.
        decision = runtime.task_router.route(tasks[0])
        # The sec-specialist should be preferred because of domain trust.
        assert decision.selected_agent_id == "sec-specialist"


# ---------------------------------------------------------------------------
# Task: Goal creation -> activation -> auto-route -> verify decisions
# ---------------------------------------------------------------------------


class TestAutoRouteGoal:
    def test_create_activate_autoroute(self, runtime: CLIRuntime):
        # Register a frontend agent.
        runtime.register_agent(
            agent_id="frontend-agent",
            name="Frontend Agent",
            capabilities=["frontend"],
            languages=["typescript"],
            frameworks=["react"],
        )

        goal = runtime.create_goal(
            title="Build UI dashboard",
            description="Create a frontend dashboard with React components",
            priority="medium",
        )
        runtime.activate_goal(str(goal.goal_id))

        decisions = runtime.auto_route_goal(str(goal.goal_id))
        assert len(decisions) > 0
        for d in decisions:
            assert d.selected_agent_id is not None

    def test_route_task_by_id(self, runtime: CLIRuntime):
        runtime.register_agent(
            agent_id="be-agent",
            name="Backend Agent",
            capabilities=["backend"],
            languages=["python"],
        )
        goal = runtime.create_goal(
            title="Add API endpoint",
            description="Create a backend API endpoint for data",
            priority="low",
        )
        runtime.activate_goal(str(goal.goal_id))
        tasks = runtime.goal_manager.get_tasks(goal.goal_id)
        assert len(tasks) > 0

        decision = runtime.route_task(str(tasks[0].task_id))
        assert isinstance(decision, RouteDecision)
        assert decision.selected_agent_id is not None


# ---------------------------------------------------------------------------
# Fallback to generic agent
# ---------------------------------------------------------------------------


class TestFallbackRouting:
    def test_fallback_to_generic_when_no_specialist(self, runtime: CLIRuntime):
        """When no specialist is registered, generic agent is used."""
        goal = runtime.create_goal(
            title="Obscure task",
            description="Do something very niche and specific",
            priority="low",
        )
        runtime.activate_goal(str(goal.goal_id))
        tasks = runtime.goal_manager.get_tasks(goal.goal_id)
        assert len(tasks) > 0

        assert runtime.task_router is not None
        decision = runtime.task_router.route(tasks[0])
        assert decision.selected_agent_id == "generic"
        assert decision.fallback_used is True


# ---------------------------------------------------------------------------
# Notification events
# ---------------------------------------------------------------------------


class TestRoutingEvents:
    def test_event_types_exist(self):
        from src.notifications.models import EventType

        assert EventType.TASK_ROUTED.value == "task.routed"
        assert EventType.ROUTING_FALLBACK.value == "routing.fallback"

    def test_routing_bridge_fires_events(self, runtime: CLIRuntime):
        """Verify the bridge tracks decisions."""
        goal = runtime.create_goal(
            title="Simple task",
            description="A straightforward task",
            priority="low",
        )
        runtime.activate_goal(str(goal.goal_id))
        runtime.auto_route_goal(str(goal.goal_id))

        assert runtime.routing_bridge is not None
        assert len(runtime.routing_bridge.decisions) > 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestAgentRegistrationPersistence:
    def test_memory_repo_save_and_load(self):
        from src.storage.memory import MemoryAgentRegistrationRepository

        repo = MemoryAgentRegistrationRepository()
        reg = AgentRegistration(
            agent_id="persist-test",
            name="Persist Test",
            capabilities=[AgentCapability.BACKEND],
            primary_capability=AgentCapability.BACKEND,
        )
        repo.save(reg)
        assert repo.get("persist-test") is not None
        assert len(repo.list_all()) == 1

        repo.delete("persist-test")
        assert repo.get("persist-test") is None

    def test_registry_loads_from_repo(self):
        from src.storage.memory import MemoryAgentRegistrationRepository

        repo = MemoryAgentRegistrationRepository()
        reg = AgentRegistration(
            agent_id="preloaded",
            name="Preloaded Agent",
            capabilities=[AgentCapability.SECURITY],
            primary_capability=AgentCapability.SECURITY,
        )
        repo.save(reg)

        # Create a new registry pointing at the repo — should load the agent.
        registry = AgentRegistry(registration_repo=repo)
        agent = registry.get("preloaded")
        assert agent.agent_id == "preloaded"

    def test_registry_persists_on_register(self):
        from src.storage.memory import MemoryAgentRegistrationRepository

        repo = MemoryAgentRegistrationRepository()
        registry = AgentRegistry(registration_repo=repo)
        reg = AgentRegistration(
            agent_id="new-one",
            name="New One",
            capabilities=[AgentCapability.QA],
            primary_capability=AgentCapability.QA,
        )
        registry.register(reg)
        assert repo.get("new-one") is not None

    def test_registry_persists_on_unregister(self):
        from src.storage.memory import MemoryAgentRegistrationRepository

        repo = MemoryAgentRegistrationRepository()
        registry = AgentRegistry(registration_repo=repo)
        reg = AgentRegistration(
            agent_id="temp",
            name="Temp",
            capabilities=[AgentCapability.DEVOPS],
            primary_capability=AgentCapability.DEVOPS,
        )
        registry.register(reg)
        registry.unregister("temp")
        assert repo.get("temp") is None
