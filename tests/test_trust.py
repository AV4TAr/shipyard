"""Tests for the Trust & Risk Scoring system."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.intent.schema import IntentDeclaration
from src.trust.models import (
    AgentProfile,
    DeployRoute,
    RiskAssessment,
    RiskFactor,
    RiskLevel,
)
from src.trust.scorer import RiskScorer
from src.trust.tracker import TrustTracker
from src.validation.models import SignalResult, ValidationSignal, ValidationVerdict


# ======================================================================
# Helpers
# ======================================================================


def _make_intent(
    *,
    target_files: list[str] | None = None,
    target_services: list[str] | None = None,
) -> IntentDeclaration:
    return IntentDeclaration(
        agent_id="agent-1",
        description="test change",
        rationale="testing",
        target_files=target_files or ["src/app.py"],
        target_services=target_services or [],
    )


def _make_verdict(
    *,
    passed: bool = True,
    confidence: float = 0.9,
    n_signals: int = 1,
) -> ValidationVerdict:
    signals = [
        SignalResult(
            signal=ValidationSignal.STATIC_ANALYSIS,
            passed=passed,
            confidence=confidence,
            findings=[],
            duration_seconds=1.0,
        )
        for _ in range(n_signals)
    ]
    return ValidationVerdict(
        intent_id=str(uuid.uuid4()),
        signals=signals,
        overall_passed=passed,
        risk_score=1.0 - confidence,
    )


def _make_experienced_profile() -> AgentProfile:
    return AgentProfile(
        agent_id="veteran",
        total_deployments=80,
        successful_deployments=76,
        rollbacks=2,
    )


# ======================================================================
# Models
# ======================================================================


class TestAgentProfile:
    def test_new_agent_has_low_trust(self) -> None:
        profile = AgentProfile(agent_id="new")
        assert profile.trust_score == 0.1
        assert profile.success_rate == 0.0

    def test_experienced_agent_has_high_trust(self) -> None:
        profile = _make_experienced_profile()
        assert profile.success_rate == 76 / 80
        assert profile.trust_score > 0.8

    def test_trust_score_is_bounded(self) -> None:
        profile = AgentProfile(
            agent_id="perfect",
            total_deployments=200,
            successful_deployments=200,
            rollbacks=0,
        )
        assert 0.0 <= profile.trust_score <= 1.0

    def test_trust_score_reflects_rollbacks(self) -> None:
        good = AgentProfile(
            agent_id="good",
            total_deployments=50,
            successful_deployments=48,
            rollbacks=1,
        )
        bad = AgentProfile(
            agent_id="bad",
            total_deployments=50,
            successful_deployments=30,
            rollbacks=15,
        )
        assert good.trust_score > bad.trust_score


class TestRiskFactor:
    def test_basic_construction(self) -> None:
        factor = RiskFactor(
            name="test", weight=0.5, score=0.7, description="a test factor"
        )
        assert factor.name == "test"
        assert factor.weight == 0.5
        assert factor.score == 0.7


# ======================================================================
# RiskScorer
# ======================================================================


class TestRiskScorer:
    def setup_method(self) -> None:
        self.scorer = RiskScorer()
        self.trusted_profile = _make_experienced_profile()
        self.new_profile = AgentProfile(agent_id="newbie")

    # ---- Risk levels for different scenarios ----

    def test_low_risk_for_test_files(self) -> None:
        intent = _make_intent(target_files=["tests/test_foo.py"])
        verdict = _make_verdict(passed=True, confidence=0.95)
        assessment = self.scorer.assess(intent, verdict, self.trusted_profile)
        assert assessment.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM)
        assert assessment.risk_score < 0.5

    def test_high_risk_for_migration_files(self) -> None:
        intent = _make_intent(
            target_files=["db/migrations/001_add_table.sql"],
            target_services=["db", "api", "worker"],
        )
        verdict = _make_verdict(passed=True, confidence=0.6)
        assessment = self.scorer.assess(intent, verdict, self.new_profile)
        assert assessment.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        assert assessment.risk_score >= 0.6

    def test_critical_risk_for_infra_with_low_confidence(self) -> None:
        intent = _make_intent(
            target_files=["infra/terraform/main.tf", "deploy/prod/config.yaml"],
            target_services=["infra", "api", "db", "cache", "worker"],
        )
        verdict = _make_verdict(passed=False, confidence=0.3)
        assessment = self.scorer.assess(intent, verdict, self.new_profile)
        assert assessment.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        assert assessment.risk_score >= 0.7

    # ---- Deploy route mapping ----

    def test_low_risk_routes_to_auto_deploy(self) -> None:
        assert self.scorer.route_map[RiskLevel.LOW] == DeployRoute.AUTO_DEPLOY

    def test_medium_risk_routes_to_agent_review(self) -> None:
        assert self.scorer.route_map[RiskLevel.MEDIUM] == DeployRoute.AGENT_REVIEW

    def test_high_risk_routes_to_human_approval(self) -> None:
        assert self.scorer.route_map[RiskLevel.HIGH] == DeployRoute.HUMAN_APPROVAL

    def test_critical_risk_routes_to_canary(self) -> None:
        assert (
            self.scorer.route_map[RiskLevel.CRITICAL]
            == DeployRoute.HUMAN_APPROVAL_CANARY
        )

    def test_assessment_route_matches_level(self) -> None:
        intent = _make_intent(target_files=["tests/test_foo.py"])
        verdict = _make_verdict(passed=True, confidence=0.95)
        assessment = self.scorer.assess(intent, verdict, self.trusted_profile)
        expected_route = self.scorer.route_map[assessment.risk_level]
        assert assessment.recommended_route == expected_route

    # ---- Factor behaviour ----

    def test_untrusted_agent_increases_risk(self) -> None:
        intent = _make_intent(target_files=["src/app.py"])
        verdict = _make_verdict(passed=True, confidence=0.8)

        risk_new = self.scorer.assess(intent, verdict, self.new_profile)
        risk_vet = self.scorer.assess(intent, verdict, self.trusted_profile)

        assert risk_new.risk_score > risk_vet.risk_score

    def test_failed_validation_increases_risk(self) -> None:
        intent = _make_intent(target_files=["src/app.py"])
        profile = self.trusted_profile

        good_verdict = _make_verdict(passed=True, confidence=0.9)
        bad_verdict = _make_verdict(passed=False, confidence=0.3)

        risk_good = self.scorer.assess(intent, good_verdict, profile)
        risk_bad = self.scorer.assess(intent, bad_verdict, profile)

        assert risk_bad.risk_score > risk_good.risk_score

    def test_more_services_increases_blast_radius(self) -> None:
        verdict = _make_verdict(passed=True, confidence=0.9)
        profile = self.trusted_profile

        one_svc = self.scorer.assess(
            _make_intent(target_services=["api"]), verdict, profile
        )
        many_svc = self.scorer.assess(
            _make_intent(target_services=["api", "db", "cache", "worker"]),
            verdict,
            profile,
        )
        assert many_svc.risk_score > one_svc.risk_score

    def test_custom_thresholds(self) -> None:
        scorer = RiskScorer(
            risk_thresholds={
                RiskLevel.CRITICAL: 0.95,
                RiskLevel.HIGH: 0.80,
                RiskLevel.MEDIUM: 0.50,
                RiskLevel.LOW: 0.0,
            }
        )
        # With higher thresholds, same change should be lower risk level
        intent = _make_intent(target_files=["src/app.py"])
        verdict = _make_verdict(passed=True, confidence=0.7)
        assessment = scorer.assess(intent, verdict, self.trusted_profile)
        assert assessment.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM)

    def test_assessment_contains_all_factors(self) -> None:
        intent = _make_intent()
        verdict = _make_verdict()
        assessment = self.scorer.assess(intent, verdict, self.trusted_profile)
        factor_names = {f.name for f in assessment.factors}
        assert factor_names == {
            "file_sensitivity",
            "blast_radius",
            "agent_trust",
            "validation_confidence",
            "time_of_day",
        }


# ======================================================================
# TrustTracker
# ======================================================================


class TestTrustTracker:
    def setup_method(self) -> None:
        self.tracker = TrustTracker()

    def test_new_agent_gets_default_profile(self) -> None:
        profile = self.tracker.get_profile("agent-x")
        assert profile.agent_id == "agent-x"
        assert profile.total_deployments == 0
        assert profile.trust_score == 0.1

    def test_record_successful_deployment(self) -> None:
        self.tracker.record_outcome("agent-x", success=True, risk_score=0.3)
        profile = self.tracker.get_profile("agent-x")
        assert profile.total_deployments == 1
        assert profile.successful_deployments == 1
        assert profile.rollbacks == 0
        assert profile.avg_risk_score == pytest.approx(0.3, abs=0.01)

    def test_record_failed_deployment(self) -> None:
        self.tracker.record_outcome("agent-x", success=False, risk_score=0.7)
        profile = self.tracker.get_profile("agent-x")
        assert profile.total_deployments == 1
        assert profile.successful_deployments == 0
        assert profile.rollbacks == 1

    def test_trust_grows_with_success(self) -> None:
        for _ in range(20):
            self.tracker.record_outcome("agent-x", success=True, risk_score=0.3)
        profile = self.tracker.get_profile("agent-x")
        assert profile.trust_score > 0.8

    def test_rollback_decreases_trust(self) -> None:
        # Build up trust
        for _ in range(10):
            self.tracker.record_outcome("agent-x", success=True, risk_score=0.3)
        trust_before = self.tracker.compute_trust_score("agent-x")

        # Rollback
        self.tracker.record_outcome("agent-x", success=False, risk_score=0.5)
        trust_after = self.tracker.compute_trust_score("agent-x")

        assert trust_after < trust_before

    def test_multiple_rollbacks_severely_reduce_trust(self) -> None:
        for _ in range(10):
            self.tracker.record_outcome("agent-x", success=True, risk_score=0.3)
        for _ in range(5):
            self.tracker.record_outcome("agent-x", success=False, risk_score=0.7)

        profile = self.tracker.get_profile("agent-x")
        assert profile.trust_score < 0.7

    def test_compute_trust_score_matches_profile(self) -> None:
        self.tracker.record_outcome("agent-x", success=True, risk_score=0.4)
        assert self.tracker.compute_trust_score("agent-x") == self.tracker.get_profile(
            "agent-x"
        ).trust_score

    def test_avg_risk_score_updates_correctly(self) -> None:
        self.tracker.record_outcome("agent-x", success=True, risk_score=0.2)
        self.tracker.record_outcome("agent-x", success=True, risk_score=0.8)
        profile = self.tracker.get_profile("agent-x")
        assert profile.avg_risk_score == pytest.approx(0.5, abs=0.01)

    def test_independent_agent_profiles(self) -> None:
        self.tracker.record_outcome("agent-a", success=True, risk_score=0.1)
        self.tracker.record_outcome("agent-b", success=False, risk_score=0.9)

        a = self.tracker.get_profile("agent-a")
        b = self.tracker.get_profile("agent-b")

        assert a.successful_deployments == 1
        assert b.rollbacks == 1
        assert a.trust_score > b.trust_score
