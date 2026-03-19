"""Pipeline orchestrator — runs the full AI-native CI/CD pipeline end to end."""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from src.coordination.claims import ClaimManager
from src.coordination.queue import DeployQueue
from src.intent.registry import IntentRegistry
from src.intent.schema import IntentDeclaration
from src.sandbox.manager import SandboxManager
from src.sandbox.models import SandboxConfig, SandboxStatus
from src.trust.models import DeployRoute
from src.trust.scorer import RiskScorer
from src.trust.tracker import TrustTracker
from src.validation.gate import ValidationGate

from .models import (
    PipelineConfig,
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageResult,
)

if TYPE_CHECKING:
    from src.storage.repositories import PipelineRunRepository

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Orchestrates the full AI-native CI/CD pipeline.

    Wires together the intent registry, sandbox manager, validation gate,
    risk scorer, trust tracker, claim manager, and deploy queue into a
    single coherent flow.

    Parameters:
        intent_registry: Validates and stores agent intents.
        sandbox_manager: Creates and manages ephemeral sandbox environments.
        validation_gate: Runs multi-signal validation on sandbox results.
        risk_scorer: Computes risk assessments for changes.
        trust_tracker: Maintains agent trust profiles.
        claim_manager: Manages agent code-area claims.
        deploy_queue: Priority queue for approved deployments.
        config: Pipeline configuration (timeouts, thresholds, etc.).
        run_repo: Optional repository for persisting pipeline runs.
        event_dispatcher: Optional dispatcher for notification events.
    """

    def __init__(
        self,
        *,
        intent_registry: IntentRegistry,
        sandbox_manager: SandboxManager,
        validation_gate: ValidationGate,
        risk_scorer: RiskScorer,
        trust_tracker: TrustTracker,
        claim_manager: ClaimManager,
        deploy_queue: DeployQueue,
        config: PipelineConfig | None = None,
        run_repo: PipelineRunRepository | None = None,
        event_dispatcher: Any | None = None,
        worktree_manager: Any | None = None,
    ) -> None:
        self.intent_registry = intent_registry
        self.sandbox_manager = sandbox_manager
        self.validation_gate = validation_gate
        self.risk_scorer = risk_scorer
        self.trust_tracker = trust_tracker
        self.claim_manager = claim_manager
        self.deploy_queue = deploy_queue
        self.config = config or PipelineConfig()
        self._run_repo = run_repo
        self._event_dispatcher = event_dispatcher
        self._worktree_manager = worktree_manager

        self._runs: dict[uuid.UUID, PipelineRun] = {}

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _save_run(self, run: PipelineRun) -> None:
        if self._run_repo:
            self._run_repo.save(run)
        self._runs[run.run_id] = run

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, intent_declaration: IntentDeclaration, agent_id: str) -> PipelineRun:
        """Orchestrate the full pipeline for a single intent.

        Stages executed in order:
        1. INTENT    — validate and register the intent
        2. SANDBOX   — create sandbox and run tests
        3. VALIDATION — run multi-signal validation gate
        4. TRUST_ROUTING — compute risk and determine deploy route
        5. DEPLOY    — route to auto-deploy, agent review, or human approval

        If any stage fails, the pipeline stops and returns with FAILED status.

        Args:
            intent_declaration: The agent's declared intent.
            agent_id: Identifier of the agent submitting the change.

        Returns:
            A :class:`PipelineRun` containing results for all executed stages.
        """
        pipeline_run = PipelineRun(
            intent_id=intent_declaration.intent_id,
            agent_id=agent_id,
            status=PipelineStatus.IN_PROGRESS,
        )
        # Store intent info so the UI can show what the agent submitted
        pipeline_run.metadata["intent_description"] = intent_declaration.description
        pipeline_run.metadata["intent_files"] = list(intent_declaration.target_files)
        if intent_declaration.metadata:
            pipeline_run.metadata.update(intent_declaration.metadata)
        self._save_run(pipeline_run)
        self._dispatch("pipeline.started", {
            "run_id": str(pipeline_run.run_id),
            "agent_id": agent_id,
            "intent_id": str(intent_declaration.intent_id),
        })

        # Stage 1: Intent validation
        intent_result = self._run_intent_stage(intent_declaration, pipeline_run)
        pipeline_run.record_stage(intent_result)
        if intent_result.status == PipelineStatus.FAILED:
            self._dispatch_failure(pipeline_run, "intent")
            self._save_run(pipeline_run)
            return pipeline_run

        # Stage 2: Sandbox execution
        sandbox_result = self._run_sandbox_stage(intent_declaration, pipeline_run)
        pipeline_run.record_stage(sandbox_result)
        if sandbox_result.status == PipelineStatus.FAILED:
            self._dispatch_failure(pipeline_run, "sandbox")
            self._save_run(pipeline_run)
            return pipeline_run

        # Stage 3: Validation gate
        validation_result = self._run_validation_stage(intent_declaration, pipeline_run)
        pipeline_run.record_stage(validation_result)
        if validation_result.status == PipelineStatus.FAILED:
            self._dispatch_failure(pipeline_run, "validation")
            self._save_run(pipeline_run)
            return pipeline_run

        # Stage 4: Trust-based routing
        routing_result = self._run_trust_routing_stage(
            intent_declaration, agent_id, pipeline_run
        )
        pipeline_run.record_stage(routing_result)
        if routing_result.status == PipelineStatus.FAILED:
            self._dispatch_failure(pipeline_run, "trust_routing")
            self._save_run(pipeline_run)
            return pipeline_run

        # Stage 5: Deploy
        deploy_result = self._run_deploy_stage(intent_declaration, agent_id, pipeline_run)
        pipeline_run.record_stage(deploy_result)
        if deploy_result.status == PipelineStatus.FAILED:
            self._dispatch_failure(pipeline_run, "deploy")
            self._save_run(pipeline_run)
            return pipeline_run

        # Deploy stage may have set status to BLOCKED (human approval needed)
        if pipeline_run.status == PipelineStatus.BLOCKED:
            from datetime import datetime, timezone

            pipeline_run.completed_at = datetime.now(timezone.utc)
            self._save_run(pipeline_run)
            self._dispatch("approval.needed", {
                "run_id": str(pipeline_run.run_id),
                "agent_id": agent_id,
            })
        else:
            pipeline_run.mark_completed(PipelineStatus.PASSED)
            self._save_run(pipeline_run)
            self._dispatch("pipeline.passed", {
                "run_id": str(pipeline_run.run_id),
                "agent_id": agent_id,
            })

        return pipeline_run

    def get_run(self, run_id: uuid.UUID) -> PipelineRun | None:
        """Retrieve a pipeline run by its ID."""
        if self._run_repo:
            run = self._run_repo.get(run_id)
            if run is not None:
                return run
        return self._runs.get(run_id)

    def list_runs(self, agent_id: str | None = None) -> list[PipelineRun]:
        """List all pipeline runs, optionally filtered by agent_id."""
        if self._run_repo:
            return self._run_repo.list_all(agent_id=agent_id)
        runs = list(self._runs.values())
        if agent_id is not None:
            runs = [r for r in runs if r.agent_id == agent_id]
        return runs

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def _run_intent_stage(
        self, intent: IntentDeclaration, pipeline_run: PipelineRun
    ) -> StageResult:
        """Stage 1: Validate the intent declaration via the registry."""
        start = time.monotonic()
        try:
            verdict = self.intent_registry.register(intent)
            elapsed = time.monotonic() - start

            if verdict.approved:
                return StageResult(
                    stage=PipelineStage.INTENT,
                    status=PipelineStatus.PASSED,
                    duration_seconds=elapsed,
                    output={
                        "approved": True,
                        "risk_level": verdict.risk_level.value,
                        "conditions": verdict.conditions,
                        "conflicts": [str(c) for c in verdict.conflicts],
                    },
                )
            else:
                return StageResult(
                    stage=PipelineStage.INTENT,
                    status=PipelineStatus.FAILED,
                    duration_seconds=elapsed,
                    output={
                        "approved": False,
                        "risk_level": verdict.risk_level.value,
                        "denial_reasons": verdict.denial_reasons,
                    },
                    error="Intent denied: " + "; ".join(verdict.denial_reasons),
                )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.exception("Intent stage failed unexpectedly")
            return StageResult(
                stage=PipelineStage.INTENT,
                status=PipelineStatus.FAILED,
                duration_seconds=elapsed,
                output={},
                error=f"Unexpected error in intent stage: {exc}",
            )

    def _run_sandbox_stage(
        self, intent: IntentDeclaration, pipeline_run: PipelineRun
    ) -> StageResult:
        """Stage 2: Create sandbox and run tests.

        If ``pipeline_run.metadata`` contains a ``worktree_path`` key and a
        worktree manager is configured, real tests are executed inside the
        git worktree.  Otherwise, the existing simulated sandbox is used.
        """
        start = time.monotonic()

        # --- Real worktree-based testing path ---
        worktree_path = pipeline_run.metadata.get("worktree_path")
        if worktree_path and self._worktree_manager is not None:
            try:
                test_command = pipeline_run.metadata.get("test_command", "pytest")
                result = self._worktree_manager.run_tests(
                    worktree_path, test_command=test_command
                )
                elapsed = time.monotonic() - start

                sandbox_output: dict[str, Any] = {
                    "sandbox_id": f"worktree:{worktree_path}",
                    "status": "succeeded" if result["passed"] else "failed",
                    "logs": result.get("stdout", "") + result.get("stderr", ""),
                    "duration_seconds": elapsed,
                    "worktree": True,
                    "returncode": result["returncode"],
                    "path": worktree_path,
                    "worktree_path": worktree_path,
                }
                if result["passed"]:
                    sandbox_output["test_results"] = {
                        "total": 1,
                        "passed": 1,
                        "failed": 0,
                        "skipped": 0,
                        "failures": [],
                    }
                else:
                    sandbox_output["test_results"] = {
                        "total": 1,
                        "passed": 0,
                        "failed": 1,
                        "skipped": 0,
                        "failures": [
                            {
                                "test_name": "worktree_tests",
                                "message": result.get("stderr", "Tests failed"),
                                "structured_error": None,
                            }
                        ],
                    }
                pipeline_run.metadata["sandbox_result"] = sandbox_output

                status = PipelineStatus.PASSED if result["passed"] else PipelineStatus.FAILED
                error = None if result["passed"] else f"Worktree tests failed (exit {result['returncode']})"

                return StageResult(
                    stage=PipelineStage.SANDBOX,
                    status=status,
                    duration_seconds=elapsed,
                    output=sandbox_output,
                    error=error,
                )
            except Exception as exc:
                elapsed = time.monotonic() - start
                logger.exception("Worktree sandbox stage failed unexpectedly")
                return StageResult(
                    stage=PipelineStage.SANDBOX,
                    status=PipelineStatus.FAILED,
                    duration_seconds=elapsed,
                    output={},
                    error=f"Unexpected error in worktree sandbox stage: {exc}",
                )

        # --- Simulated sandbox path (existing behaviour) ---
        try:
            config = SandboxConfig(
                intent_id=intent.intent_id,
                timeout_seconds=self.config.sandbox_timeout,
            )
            sandbox_id = self.sandbox_manager.create(config)
            result = self.sandbox_manager.execute(sandbox_id, "pytest")
            elapsed = time.monotonic() - start

            # Store sandbox output in pipeline metadata for later stages
            sandbox_output: dict[str, Any] = {
                "sandbox_id": str(sandbox_id),
                "status": result.status.value,
                "logs": result.logs,
                "duration_seconds": result.duration_seconds,
            }
            if result.test_results:
                sandbox_output["test_results"] = {
                    "total": result.test_results.total,
                    "passed": result.test_results.passed,
                    "failed": result.test_results.failed,
                    "skipped": result.test_results.skipped,
                    "failures": [
                        {
                            "test_name": f.test_name,
                            "message": f.message,
                            "structured_error": f.structured_error,
                        }
                        for f in result.test_results.failures
                    ],
                }
            pipeline_run.metadata["sandbox_result"] = sandbox_output

            if result.status == SandboxStatus.SUCCEEDED:
                status = PipelineStatus.PASSED
                error = None
            else:
                status = PipelineStatus.FAILED
                error = f"Sandbox execution {result.status.value}"

            try:
                self.sandbox_manager.destroy(sandbox_id)
            except Exception:
                logger.warning("Failed to destroy sandbox %s", sandbox_id)

            return StageResult(
                stage=PipelineStage.SANDBOX,
                status=status,
                duration_seconds=elapsed,
                output=sandbox_output,
                error=error,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.exception("Sandbox stage failed unexpectedly")
            return StageResult(
                stage=PipelineStage.SANDBOX,
                status=PipelineStatus.FAILED,
                duration_seconds=elapsed,
                output={},
                error=f"Unexpected error in sandbox stage: {exc}",
            )

    def _run_validation_stage(
        self, intent: IntentDeclaration, pipeline_run: PipelineRun
    ) -> StageResult:
        """Stage 3: Run multi-signal validation gate."""
        start = time.monotonic()
        try:
            # Pass sandbox results to validation signals
            sandbox_data = dict(pipeline_run.metadata.get("sandbox_result", {}))
            # Ensure validation runners know the worktree path
            wt_path = pipeline_run.metadata.get("worktree_path")
            if wt_path:
                sandbox_data["path"] = wt_path
                sandbox_data["worktree_path"] = wt_path
            verdict = self.validation_gate.validate(
                str(intent.intent_id), sandbox_data
            )
            elapsed = time.monotonic() - start

            validation_output: dict[str, Any] = {
                "overall_passed": verdict.overall_passed,
                "risk_score": verdict.risk_score,
                "signals": [
                    {
                        "signal": s.signal.value,
                        "passed": s.passed,
                        "confidence": s.confidence,
                        "findings_count": len(s.findings),
                        "findings": [
                            {
                                "severity": f.severity.value,
                                "title": f.title,
                                "description": f.description,
                                "file_path": f.file_path,
                                "line_number": f.line_number,
                                "suggestion": f.suggestion,
                            }
                            for f in s.findings
                        ],
                    }
                    for s in verdict.signals
                ],
                "blocking_findings": [
                    {
                        "severity": f.severity.value,
                        "title": f.title,
                        "description": f.description,
                        "file_path": f.file_path,
                        "line_number": f.line_number,
                        "suggestion": f.suggestion,
                    }
                    for f in verdict.blocking_findings
                ],
                "recommendations": verdict.recommendations,
            }
            pipeline_run.metadata["validation_verdict"] = validation_output

            if verdict.overall_passed:
                return StageResult(
                    stage=PipelineStage.VALIDATION,
                    status=PipelineStatus.PASSED,
                    duration_seconds=elapsed,
                    output=validation_output,
                )
            else:
                return StageResult(
                    stage=PipelineStage.VALIDATION,
                    status=PipelineStatus.FAILED,
                    duration_seconds=elapsed,
                    output=validation_output,
                    error="Validation gate did not pass: "
                    + "; ".join(verdict.recommendations),
                )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.exception("Validation stage failed unexpectedly")
            return StageResult(
                stage=PipelineStage.VALIDATION,
                status=PipelineStatus.FAILED,
                duration_seconds=elapsed,
                output={},
                error=f"Unexpected error in validation stage: {exc}",
            )

    def _run_trust_routing_stage(
        self,
        intent: IntentDeclaration,
        agent_id: str,
        pipeline_run: PipelineRun,
    ) -> StageResult:
        """Stage 4: Compute risk and determine deploy route."""
        start = time.monotonic()
        try:
            agent_profile = self.trust_tracker.get_profile(agent_id)

            # Reconstruct a minimal ValidationVerdict for the risk scorer
            from src.validation.models import ValidationVerdict

            validation_data = pipeline_run.metadata.get("validation_verdict", {})
            validation_verdict = ValidationVerdict(
                intent_id=str(intent.intent_id),
                overall_passed=validation_data.get("overall_passed", False),
                risk_score=validation_data.get("risk_score", 0.5),
            )

            assessment = self.risk_scorer.assess(
                intent, validation_verdict, agent_profile
            )
            elapsed = time.monotonic() - start

            routing_output: dict[str, Any] = {
                "risk_level": assessment.risk_level.value,
                "risk_score": assessment.risk_score,
                "recommended_route": assessment.recommended_route.value,
                "factors": [
                    {
                        "name": f.name,
                        "weight": f.weight,
                        "score": f.score,
                        "description": f.description,
                    }
                    for f in assessment.factors
                ],
                "agent_trust_score": agent_profile.trust_score,
            }
            pipeline_run.metadata["risk_assessment"] = routing_output

            return StageResult(
                stage=PipelineStage.TRUST_ROUTING,
                status=PipelineStatus.PASSED,
                duration_seconds=elapsed,
                output=routing_output,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.exception("Trust routing stage failed unexpectedly")
            return StageResult(
                stage=PipelineStage.TRUST_ROUTING,
                status=PipelineStatus.FAILED,
                duration_seconds=elapsed,
                output={},
                error=f"Unexpected error in trust routing stage: {exc}",
            )

    def _run_deploy_stage(
        self,
        intent: IntentDeclaration,
        agent_id: str,
        pipeline_run: PipelineRun,
    ) -> StageResult:
        """Stage 5: Route to auto-deploy, agent review, or human approval queue."""
        start = time.monotonic()
        try:
            routing_data = pipeline_run.metadata.get("risk_assessment", {})
            route = routing_data.get("recommended_route", DeployRoute.HUMAN_APPROVAL.value)

            deploy_output: dict[str, Any] = {
                "route": route,
                "intent_id": str(intent.intent_id),
                "agent_id": agent_id,
            }

            if route == DeployRoute.AUTO_DEPLOY.value:
                deploy_output["action"] = "auto_deployed"
                deploy_output["message"] = (
                    "Change auto-deployed (low risk, sufficient trust)."
                )
                status = PipelineStatus.PASSED
                # Commit worktree changes on auto-deploy
                self._try_worktree_commit(intent, pipeline_run, deploy_output)
            elif route == DeployRoute.AGENT_REVIEW.value:
                deploy_output["action"] = "queued_for_agent_review"
                deploy_output["message"] = (
                    "Change queued for supervisor agent review before deploy."
                )
                status = PipelineStatus.PASSED
                # Commit worktree changes on agent-review (passed)
                self._try_worktree_commit(intent, pipeline_run, deploy_output)
            elif route == DeployRoute.HUMAN_APPROVAL.value:
                deploy_output["action"] = "queued_for_human_approval"
                deploy_output["message"] = (
                    "Change requires human approval before deployment."
                )
                status = PipelineStatus.BLOCKED
            elif route == DeployRoute.HUMAN_APPROVAL_CANARY.value:
                deploy_output["action"] = "queued_for_human_approval_canary"
                deploy_output["message"] = (
                    "Critical change requires human approval with canary deployment."
                )
                status = PipelineStatus.BLOCKED
            else:
                deploy_output["action"] = "unknown_route"
                deploy_output["message"] = f"Unknown deploy route: {route}"
                status = PipelineStatus.FAILED

            elapsed = time.monotonic() - start
            return StageResult(
                stage=PipelineStage.DEPLOY,
                status=status,
                duration_seconds=elapsed,
                output=deploy_output,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.exception("Deploy stage failed unexpectedly")
            return StageResult(
                stage=PipelineStage.DEPLOY,
                status=PipelineStatus.FAILED,
                duration_seconds=elapsed,
                output={},
                error=f"Unexpected error in deploy stage: {exc}",
            )

    # ------------------------------------------------------------------
    # Worktree helpers
    # ------------------------------------------------------------------

    def _try_worktree_commit(
        self,
        intent: IntentDeclaration,
        pipeline_run: PipelineRun,
        deploy_output: dict[str, Any],
    ) -> None:
        """Commit worktree changes if worktree metadata is present."""
        worktree_path = pipeline_run.metadata.get("worktree_path")
        branch_name = pipeline_run.metadata.get("branch_name")
        if not (worktree_path and branch_name and self._worktree_manager):
            return
        try:
            commit_hash = self._worktree_manager.commit(
                worktree_path, f"Deploy: {intent.description}"
            )
            deploy_output["worktree_committed"] = True
            deploy_output["commit_hash"] = commit_hash
        except Exception:
            logger.warning(
                "Failed to commit worktree changes at %s", worktree_path, exc_info=True
            )

    # ------------------------------------------------------------------
    # Event dispatch helpers
    # ------------------------------------------------------------------

    def _dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        """Fire a notification event if a dispatcher is configured."""
        if self._event_dispatcher is None:
            return
        try:
            from datetime import datetime, timezone

            from src.notifications.models import Event, EventType

            event = Event(
                event_type=EventType(event_type),
                timestamp=datetime.now(timezone.utc),
                data=data,
            )
            self._event_dispatcher.dispatch(event)
        except Exception:
            logger.debug("Failed to dispatch event %s", event_type, exc_info=True)

    def _dispatch_failure(self, run: PipelineRun, stage: str) -> None:
        """Dispatch a pipeline.failed event."""
        self._dispatch("pipeline.failed", {
            "run_id": str(run.run_id),
            "agent_id": run.agent_id,
            "stage": stage,
        })
