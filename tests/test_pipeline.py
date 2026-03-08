"""Tests for the Pipeline Orchestrator and FeedbackFormatter."""

from __future__ import annotations

import uuid

import pytest

from src.coordination.claims import ClaimManager
from src.coordination.queue import DeployQueue
from src.intent.registry import IntentRegistry
from src.intent.schema import IntentDeclaration, ScopeConstraint
from src.sandbox.manager import SandboxManager
from src.trust.scorer import RiskScorer
from src.trust.tracker import TrustTracker
from src.validation.gate import ValidationGate
from src.validation.signals import (
    BehavioralDiffRunner,
    IntentAlignmentRunner,
    ResourceBoundsRunner,
    SecurityScanRunner,
    StaticAnalysisRunner,
)

from src.pipeline.feedback import FeedbackFormatter
from src.pipeline.models import (
    PipelineConfig,
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageResult,
)
from src.pipeline.orchestrator import PipelineOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_intent(
    *,
    agent_id: str = "agent-1",
    target_files: list[str] | None = None,
    description: str = "Add caching to user service",
    rationale: str = "Improve response time by 50%",
) -> IntentDeclaration:
    return IntentDeclaration(
        agent_id=agent_id,
        description=description,
        rationale=rationale,
        target_files=target_files or ["src/app/cache.py"],
    )


def _make_orchestrator(
    *,
    constraints: list[ScopeConstraint] | None = None,
    force_validation_pass: bool = True,
    config: PipelineConfig | None = None,
) -> PipelineOrchestrator:
    """Build a fully-wired orchestrator with simulated components."""
    runners = [
        StaticAnalysisRunner(force_pass=force_validation_pass),
        BehavioralDiffRunner(force_pass=force_validation_pass),
        IntentAlignmentRunner(force_pass=force_validation_pass),
        ResourceBoundsRunner(force_pass=force_validation_pass),
        SecurityScanRunner(force_pass=force_validation_pass),
    ]

    return PipelineOrchestrator(
        intent_registry=IntentRegistry(constraints=constraints or []),
        sandbox_manager=SandboxManager(),
        validation_gate=ValidationGate(runners),
        risk_scorer=RiskScorer(),
        trust_tracker=TrustTracker(),
        claim_manager=ClaimManager(),
        deploy_queue=DeployQueue(),
        config=config or PipelineConfig(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestPipelineHappyPath:
    """Full pipeline: intent -> sandbox -> validation -> trust routing -> deploy."""

    def test_full_pipeline_passes(self) -> None:
        orchestrator = _make_orchestrator()
        intent = _make_intent()

        result = orchestrator.run(intent, "agent-1")

        # Pipeline should complete (PASSED or BLOCKED depending on route)
        assert result.status in (PipelineStatus.PASSED, PipelineStatus.BLOCKED)
        assert result.intent_id == intent.intent_id
        assert result.agent_id == "agent-1"

        # All five stages should have results
        assert PipelineStage.INTENT in result.stage_results
        assert PipelineStage.SANDBOX in result.stage_results
        assert PipelineStage.VALIDATION in result.stage_results
        assert PipelineStage.TRUST_ROUTING in result.stage_results
        assert PipelineStage.DEPLOY in result.stage_results

        # Intent stage passed
        intent_result = result.stage_results[PipelineStage.INTENT]
        assert intent_result.status == PipelineStatus.PASSED
        assert intent_result.output["approved"] is True

        # Sandbox stage passed
        sandbox_result = result.stage_results[PipelineStage.SANDBOX]
        assert sandbox_result.status == PipelineStatus.PASSED

        # Validation stage passed
        val_result = result.stage_results[PipelineStage.VALIDATION]
        assert val_result.status == PipelineStatus.PASSED

        # Trust routing produced a route
        routing_result = result.stage_results[PipelineStage.TRUST_ROUTING]
        assert routing_result.status == PipelineStatus.PASSED
        assert "recommended_route" in routing_result.output

    def test_pipeline_run_has_timestamps(self) -> None:
        orchestrator = _make_orchestrator()
        intent = _make_intent()

        result = orchestrator.run(intent, "agent-1")

        assert result.started_at is not None
        assert result.completed_at is not None

    def test_each_stage_has_duration(self) -> None:
        orchestrator = _make_orchestrator()
        intent = _make_intent()

        result = orchestrator.run(intent, "agent-1")

        for stage_result in result.stage_results.values():
            assert stage_result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Intent denial stops pipeline
# ---------------------------------------------------------------------------


class TestPipelineIntentDenial:
    """Pipeline stops immediately when intent is denied."""

    def test_denied_intent_stops_pipeline(self) -> None:
        constraint = ScopeConstraint(
            agent_id="agent-1",
            allowed_paths=["lib/**"],  # agent tries to touch src/ which is not allowed
            denied_paths=[],
        )
        orchestrator = _make_orchestrator(constraints=[constraint])
        intent = _make_intent(target_files=["src/app/main.py"])

        result = orchestrator.run(intent, "agent-1")

        assert result.status == PipelineStatus.FAILED

        # Intent stage should be FAILED
        intent_result = result.stage_results[PipelineStage.INTENT]
        assert intent_result.status == PipelineStatus.FAILED
        assert intent_result.output["approved"] is False
        assert len(intent_result.output["denial_reasons"]) > 0

        # Sandbox and later stages should not have been executed
        assert PipelineStage.SANDBOX not in result.stage_results
        assert PipelineStage.VALIDATION not in result.stage_results

    def test_denied_intent_error_message(self) -> None:
        constraint = ScopeConstraint(
            agent_id="agent-1",
            allowed_paths=["lib/**"],
        )
        orchestrator = _make_orchestrator(constraints=[constraint])
        intent = _make_intent(target_files=["src/app/main.py"])

        result = orchestrator.run(intent, "agent-1")

        intent_result = result.stage_results[PipelineStage.INTENT]
        assert intent_result.error is not None
        assert "denied" in intent_result.error.lower()


# ---------------------------------------------------------------------------
# Validation failure stops pipeline
# ---------------------------------------------------------------------------


class TestPipelineValidationFailure:
    """Pipeline stops when validation gate fails."""

    def test_validation_failure_stops_pipeline(self) -> None:
        orchestrator = _make_orchestrator(force_validation_pass=False)
        intent = _make_intent()

        result = orchestrator.run(intent, "agent-1")

        assert result.status == PipelineStatus.FAILED

        # Intent and sandbox should have passed
        assert result.stage_results[PipelineStage.INTENT].status == PipelineStatus.PASSED
        assert result.stage_results[PipelineStage.SANDBOX].status == PipelineStatus.PASSED

        # Validation should have failed
        val_result = result.stage_results[PipelineStage.VALIDATION]
        assert val_result.status == PipelineStatus.FAILED
        assert val_result.output["overall_passed"] is False

        # Later stages should not have executed
        assert PipelineStage.TRUST_ROUTING not in result.stage_results
        assert PipelineStage.DEPLOY not in result.stage_results

    def test_validation_failure_includes_recommendations(self) -> None:
        orchestrator = _make_orchestrator(force_validation_pass=False)
        intent = _make_intent()

        result = orchestrator.run(intent, "agent-1")

        val_result = result.stage_results[PipelineStage.VALIDATION]
        assert val_result.error is not None
        assert "not pass" in val_result.error.lower() or "Validation" in val_result.error


# ---------------------------------------------------------------------------
# Feedback formatter
# ---------------------------------------------------------------------------


class TestFeedbackFormatter:
    """FeedbackFormatter produces machine-readable output."""

    def test_successful_pipeline_feedback(self) -> None:
        orchestrator = _make_orchestrator()
        intent = _make_intent()
        pipeline_run = orchestrator.run(intent, "agent-1")

        formatter = FeedbackFormatter()
        feedback = formatter.format_for_agent(pipeline_run)

        assert feedback["run_id"] == str(pipeline_run.run_id)
        assert feedback["agent_id"] == "agent-1"
        assert feedback["status"] in ("passed", "blocked")
        assert isinstance(feedback["stages"], list)
        assert isinstance(feedback["failures"], list)
        assert isinstance(feedback["suggested_fixes"], list)
        assert isinstance(feedback["next_actions"], list)
        assert isinstance(feedback["file_references"], list)

    def test_failed_pipeline_feedback_has_failures(self) -> None:
        constraint = ScopeConstraint(
            agent_id="agent-1",
            allowed_paths=["lib/**"],
        )
        orchestrator = _make_orchestrator(constraints=[constraint])
        intent = _make_intent(target_files=["src/app/main.py"])
        pipeline_run = orchestrator.run(intent, "agent-1")

        formatter = FeedbackFormatter()
        feedback = formatter.format_for_agent(pipeline_run)

        assert feedback["succeeded"] is False
        assert feedback["status"] == "failed"
        assert len(feedback["failures"]) > 0
        assert len(feedback["next_actions"]) > 0

        # Should include specific fix suggestions for intent denial
        assert any(
            fix["source"] == "intent.denial"
            for fix in feedback["suggested_fixes"]
        )

    def test_validation_failure_feedback_has_details(self) -> None:
        orchestrator = _make_orchestrator(force_validation_pass=False)
        intent = _make_intent()
        pipeline_run = orchestrator.run(intent, "agent-1")

        formatter = FeedbackFormatter()
        feedback = formatter.format_for_agent(pipeline_run)

        assert feedback["succeeded"] is False
        assert len(feedback["failures"]) > 0

        # Validation failure should have recommendations
        val_failure = next(
            (f for f in feedback["failures"] if f["stage"] == "validation"),
            None,
        )
        assert val_failure is not None
        assert "recommendations" in val_failure

    def test_feedback_stages_include_all_pipeline_stages(self) -> None:
        orchestrator = _make_orchestrator()
        intent = _make_intent()
        pipeline_run = orchestrator.run(intent, "agent-1")

        formatter = FeedbackFormatter()
        feedback = formatter.format_for_agent(pipeline_run)

        stage_names = [s["stage"] for s in feedback["stages"]]
        assert "intent" in stage_names
        assert "sandbox" in stage_names
        assert "validation" in stage_names
        assert "trust_routing" in stage_names
        assert "deploy" in stage_names
        assert "monitoring" in stage_names  # not executed, but present


# ---------------------------------------------------------------------------
# Pipeline run tracking
# ---------------------------------------------------------------------------


class TestPipelineRunTracking:
    """get_run and list_runs work correctly."""

    def test_get_run_returns_correct_run(self) -> None:
        orchestrator = _make_orchestrator()
        intent = _make_intent()
        pipeline_run = orchestrator.run(intent, "agent-1")

        retrieved = orchestrator.get_run(pipeline_run.run_id)
        assert retrieved is not None
        assert retrieved.run_id == pipeline_run.run_id

    def test_get_run_returns_none_for_unknown(self) -> None:
        orchestrator = _make_orchestrator()
        assert orchestrator.get_run(uuid.uuid4()) is None

    def test_list_runs_returns_all(self) -> None:
        orchestrator = _make_orchestrator()

        intent1 = _make_intent(agent_id="agent-1")
        intent2 = _make_intent(agent_id="agent-2")
        orchestrator.run(intent1, "agent-1")
        orchestrator.run(intent2, "agent-2")

        all_runs = orchestrator.list_runs()
        assert len(all_runs) == 2

    def test_list_runs_filters_by_agent(self) -> None:
        orchestrator = _make_orchestrator()

        intent1 = _make_intent(agent_id="agent-1")
        intent2 = _make_intent(agent_id="agent-2")
        orchestrator.run(intent1, "agent-1")
        orchestrator.run(intent2, "agent-2")

        agent1_runs = orchestrator.list_runs(agent_id="agent-1")
        assert len(agent1_runs) == 1
        assert agent1_runs[0].agent_id == "agent-1"

        agent2_runs = orchestrator.list_runs(agent_id="agent-2")
        assert len(agent2_runs) == 1
        assert agent2_runs[0].agent_id == "agent-2"

    def test_list_runs_empty_when_no_runs(self) -> None:
        orchestrator = _make_orchestrator()
        assert orchestrator.list_runs() == []

    def test_list_runs_unknown_agent_returns_empty(self) -> None:
        orchestrator = _make_orchestrator()
        intent = _make_intent()
        orchestrator.run(intent, "agent-1")

        assert orchestrator.list_runs(agent_id="nonexistent") == []
