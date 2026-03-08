"""Tests for the Intent Declaration Layer."""

from __future__ import annotations

import uuid

import pytest

from src.intent import (
    IntentDeclaration,
    IntentRegistry,
    IntentValidator,
    IntentVerdict,
    RiskLevel,
    ScopeConstraint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_intent(
    *,
    agent_id: str = "agent-1",
    target_files: list[str] | None = None,
    target_services: list[str] | None = None,
    description: str = "Update feature X",
    rationale: str = "Improves performance",
) -> IntentDeclaration:
    return IntentDeclaration(
        agent_id=agent_id,
        description=description,
        rationale=rationale,
        target_files=target_files or ["src/app/main.py"],
        target_services=target_services or [],
    )


def _make_constraint(
    *,
    agent_id: str = "agent-1",
    allowed_paths: list[str] | None = None,
    denied_paths: list[str] | None = None,
    allowed_services: list[str] | None = None,
    max_risk_level: RiskLevel = RiskLevel.HIGH,
) -> ScopeConstraint:
    return ScopeConstraint(
        agent_id=agent_id,
        allowed_paths=allowed_paths or ["src/**"],
        denied_paths=denied_paths or [],
        allowed_services=allowed_services or [],
        max_risk_level=max_risk_level,
    )


# ---------------------------------------------------------------------------
# Validation — scope
# ---------------------------------------------------------------------------

class TestScopeValidation:
    """Tests for scope-based approval / denial."""

    def test_valid_intent_approved(self) -> None:
        intent = _make_intent(target_files=["src/app/main.py"])
        constraint = _make_constraint(allowed_paths=["src/**"])
        verdict = IntentValidator().validate(intent, [constraint])

        assert verdict.approved is True
        assert verdict.denial_reasons == []

    def test_intent_denied_outside_allowed_paths(self) -> None:
        intent = _make_intent(target_files=["infra/terraform/main.tf"])
        constraint = _make_constraint(allowed_paths=["src/**"])
        verdict = IntentValidator().validate(intent, [constraint])

        assert verdict.approved is False
        assert any("not within any allowed path" in r for r in verdict.denial_reasons)

    def test_intent_denied_by_denied_path(self) -> None:
        intent = _make_intent(target_files=["src/secrets/key.pem"])
        constraint = _make_constraint(
            allowed_paths=["src/**"],
            denied_paths=["**/secrets/**"],
        )
        verdict = IntentValidator().validate(intent, [constraint])

        assert verdict.approved is False
        assert any("denied path" in r for r in verdict.denial_reasons)

    def test_intent_denied_for_disallowed_service(self) -> None:
        intent = _make_intent(target_services=["payments"])
        constraint = _make_constraint(allowed_services=["auth", "users"])
        verdict = IntentValidator().validate(intent, [constraint])

        assert verdict.approved is False
        assert any("payments" in r for r in verdict.denial_reasons)

    def test_no_constraints_means_approved(self) -> None:
        """When no scope constraints exist for the agent, the intent is allowed."""
        intent = _make_intent()
        verdict = IntentValidator().validate(intent, [])

        assert verdict.approved is True


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------

class TestRiskClassification:
    """Tests for automatic risk-level assignment."""

    def test_test_files_are_low_risk(self) -> None:
        intent = _make_intent(target_files=["tests/test_foo.py"])
        verdict = IntentValidator().validate(intent, [])

        assert verdict.risk_level == RiskLevel.LOW

    def test_migration_files_are_high_risk(self) -> None:
        intent = _make_intent(target_files=["src/db/migrations/001_init.sql"])
        verdict = IntentValidator().validate(intent, [])

        assert verdict.risk_level == RiskLevel.HIGH

    def test_docker_files_are_high_risk(self) -> None:
        intent = _make_intent(target_files=["docker-compose.yml"])
        verdict = IntentValidator().validate(intent, [])

        assert verdict.risk_level == RiskLevel.HIGH

    def test_deploy_files_are_critical(self) -> None:
        intent = _make_intent(target_files=["infra/deploy/main.tf"])
        verdict = IntentValidator().validate(intent, [])

        assert verdict.risk_level == RiskLevel.CRITICAL

    def test_env_files_are_critical(self) -> None:
        intent = _make_intent(target_files=["config/production.env"])
        verdict = IntentValidator().validate(intent, [])

        assert verdict.risk_level == RiskLevel.CRITICAL

    def test_regular_source_defaults_to_medium(self) -> None:
        intent = _make_intent(target_files=["src/app/main.py"])
        verdict = IntentValidator().validate(intent, [])

        assert verdict.risk_level == RiskLevel.MEDIUM

    def test_highest_risk_wins(self) -> None:
        """When multiple files have different risk, the highest is used."""
        intent = _make_intent(
            target_files=["tests/test_a.py", "infra/deploy/k8s.yaml"]
        )
        verdict = IntentValidator().validate(intent, [])

        assert verdict.risk_level == RiskLevel.CRITICAL

    def test_risk_exceeding_max_denied(self) -> None:
        intent = _make_intent(target_files=["src/db/migrations/002.sql"])
        constraint = _make_constraint(
            allowed_paths=["src/**"],
            max_risk_level=RiskLevel.MEDIUM,
        )
        verdict = IntentValidator().validate(intent, [constraint])

        assert verdict.approved is False
        assert any("exceeds maximum" in r for r in verdict.denial_reasons)


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

class TestConflictDetection:
    """Tests for overlapping-file conflict detection."""

    def test_no_conflict_with_disjoint_files(self) -> None:
        intent_a = _make_intent(target_files=["src/a.py"])
        intent_b = _make_intent(target_files=["src/b.py"])

        verdict = IntentValidator().validate(intent_b, [], active_intents=[intent_a])

        assert verdict.conflicts == []

    def test_conflict_with_same_file(self) -> None:
        intent_a = _make_intent(target_files=["src/a.py"])
        intent_b = _make_intent(target_files=["src/a.py"])

        verdict = IntentValidator().validate(intent_b, [], active_intents=[intent_a])

        assert intent_a.intent_id in verdict.conflicts
        assert "Conflicting intents must be resolved" in verdict.conditions[0]


# ---------------------------------------------------------------------------
# Registry lifecycle
# ---------------------------------------------------------------------------

class TestIntentRegistry:
    """Tests for the IntentRegistry register / get_active / release cycle."""

    def test_register_and_get_active(self) -> None:
        registry = IntentRegistry()
        intent = _make_intent()
        verdict = registry.register(intent)

        assert verdict.approved is True
        active = registry.get_active()
        assert len(active) == 1
        assert active[0].intent_id == intent.intent_id

    def test_release_removes_intent(self) -> None:
        registry = IntentRegistry()
        intent = _make_intent()
        registry.register(intent)

        assert registry.release(intent.intent_id) is True
        assert registry.get_active() == []

    def test_release_unknown_returns_false(self) -> None:
        registry = IntentRegistry()
        assert registry.release(uuid.uuid4()) is False

    def test_denied_intent_not_stored(self) -> None:
        constraint = _make_constraint(allowed_paths=["lib/**"])
        registry = IntentRegistry(constraints=[constraint])
        intent = _make_intent(target_files=["src/app/main.py"])
        verdict = registry.register(intent)

        assert verdict.approved is False
        assert registry.get_active() == []

    def test_get_conflicts(self) -> None:
        registry = IntentRegistry()
        intent_a = _make_intent(target_files=["src/shared.py"])
        registry.register(intent_a)

        intent_b = _make_intent(target_files=["src/shared.py"])
        conflicts = registry.get_conflicts(intent_b)

        assert intent_a.intent_id in conflicts

    def test_full_lifecycle(self) -> None:
        """Register two intents, verify conflict, release one, verify clean."""
        registry = IntentRegistry()

        intent_a = _make_intent(target_files=["src/module.py"])
        verdict_a = registry.register(intent_a)
        assert verdict_a.approved is True

        intent_b = _make_intent(
            agent_id="agent-2",
            target_files=["src/module.py"],
        )
        verdict_b = registry.register(intent_b)
        # Still approved (conflicts are conditions, not denials)
        assert verdict_b.approved is True
        assert intent_a.intent_id in verdict_b.conflicts
        assert len(registry.get_active()) == 2

        registry.release(intent_a.intent_id)
        assert len(registry.get_active()) == 1
        assert registry.get_active()[0].intent_id == intent_b.intent_id
