"""Feedback formatter — converts pipeline results into agent-consumable structured data."""

from __future__ import annotations

from typing import Any

from .models import PipelineRun, PipelineStage, PipelineStatus, StageResult


class FeedbackFormatter:
    """Converts a :class:`PipelineRun` into structured, machine-readable feedback.

    This is the key interface for AI agents to understand what happened in
    the pipeline and what they should do next.  Every piece of output is
    designed to be parsed programmatically, not read by humans.
    """

    def format_for_agent(self, pipeline_run: PipelineRun) -> dict[str, Any]:
        """Produce a complete feedback dict for the agent.

        The returned dict contains:
        - ``run_id``: The pipeline run identifier.
        - ``status``: Overall pipeline status.
        - ``succeeded``: Boolean shorthand.
        - ``current_stage``: The last stage that executed.
        - ``stages``: Per-stage summaries with outputs.
        - ``failures``: List of failure descriptions with actionable detail.
        - ``suggested_fixes``: Concrete actions the agent should take.
        - ``next_actions``: Ordered list of what to do next.
        - ``file_references``: Files/lines mentioned in findings for quick access.
        """
        failures = self._collect_failures(pipeline_run)
        fixes = self._collect_suggested_fixes(pipeline_run)
        file_refs = self._collect_file_references(pipeline_run)
        next_actions = self._determine_next_actions(pipeline_run)

        return {
            "run_id": str(pipeline_run.run_id),
            "intent_id": str(pipeline_run.intent_id) if pipeline_run.intent_id else None,
            "agent_id": pipeline_run.agent_id,
            "status": pipeline_run.status.value,
            "succeeded": pipeline_run.status == PipelineStatus.PASSED,
            "current_stage": pipeline_run.current_stage.value,
            "stages": self._format_stages(pipeline_run),
            "failures": failures,
            "suggested_fixes": fixes,
            "next_actions": next_actions,
            "file_references": file_refs,
            "metadata": pipeline_run.metadata,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _format_stages(self, pipeline_run: PipelineRun) -> list[dict[str, Any]]:
        """Build a summary for each stage that executed."""
        stages: list[dict[str, Any]] = []
        for stage in PipelineStage:
            result = pipeline_run.stage_results.get(stage)
            if result is None:
                stages.append({
                    "stage": stage.value,
                    "status": "not_executed",
                })
                continue
            stages.append({
                "stage": stage.value,
                "status": result.status.value,
                "duration_seconds": result.duration_seconds,
                "error": result.error,
                "output": result.output,
            })
        return stages

    def _collect_failures(self, pipeline_run: PipelineRun) -> list[dict[str, Any]]:
        """Extract all failure details across stages."""
        failures: list[dict[str, Any]] = []

        for stage, result in pipeline_run.stage_results.items():
            if result.status not in (PipelineStatus.FAILED, PipelineStatus.BLOCKED):
                continue

            failure: dict[str, Any] = {
                "stage": stage.value,
                "error": result.error,
            }

            # Intent-stage specifics
            if stage == PipelineStage.INTENT:
                failure["denial_reasons"] = result.output.get("denial_reasons", [])

            # Sandbox-stage specifics
            if stage == PipelineStage.SANDBOX:
                test_results = result.output.get("test_results", {})
                if test_results:
                    failure["test_failures"] = test_results.get("failures", [])
                    failure["tests_passed"] = test_results.get("passed", 0)
                    failure["tests_total"] = test_results.get("total", 0)

            # Validation-stage specifics
            if stage == PipelineStage.VALIDATION:
                failure["blocking_findings"] = result.output.get("blocking_findings", [])
                failure["recommendations"] = result.output.get("recommendations", [])
                failure["signals"] = [
                    {
                        "signal": s["signal"],
                        "passed": s["passed"],
                        "findings": s.get("findings", []),
                    }
                    for s in result.output.get("signals", [])
                    if not s["passed"]
                ]

            failures.append(failure)

        return failures

    def _collect_suggested_fixes(self, pipeline_run: PipelineRun) -> list[dict[str, Any]]:
        """Extract actionable fix suggestions from all stage outputs."""
        fixes: list[dict[str, Any]] = []

        # From validation findings
        validation_result = pipeline_run.stage_results.get(PipelineStage.VALIDATION)
        if validation_result:
            for signal_data in validation_result.output.get("signals", []):
                for finding in signal_data.get("findings", []):
                    suggestion = finding.get("suggestion")
                    if suggestion:
                        fixes.append({
                            "source": f"validation.{signal_data['signal']}",
                            "severity": finding.get("severity", "unknown"),
                            "title": finding.get("title", ""),
                            "suggestion": suggestion,
                            "file_path": finding.get("file_path"),
                            "line_number": finding.get("line_number"),
                        })

        # From sandbox test failures
        sandbox_result = pipeline_run.stage_results.get(PipelineStage.SANDBOX)
        if sandbox_result:
            test_results = sandbox_result.output.get("test_results", {})
            for failure in test_results.get("failures", []):
                structured = failure.get("structured_error", {})
                fixes.append({
                    "source": "sandbox.test_failure",
                    "severity": "error",
                    "title": f"Test failed: {failure.get('test_name', 'unknown')}",
                    "suggestion": f"Fix test {failure.get('test_name', '')}: "
                    f"{failure.get('message', '')}",
                    "file_path": structured.get("file"),
                    "line_number": structured.get("line"),
                })

        # From intent denial
        intent_result = pipeline_run.stage_results.get(PipelineStage.INTENT)
        if intent_result and intent_result.status == PipelineStatus.FAILED:
            for reason in intent_result.output.get("denial_reasons", []):
                fixes.append({
                    "source": "intent.denial",
                    "severity": "error",
                    "title": "Intent denied",
                    "suggestion": f"Resolve: {reason}",
                    "file_path": None,
                    "line_number": None,
                })

        return fixes

    def _collect_file_references(self, pipeline_run: PipelineRun) -> list[dict[str, Any]]:
        """Gather all file/line references from findings and test failures."""
        refs: list[dict[str, Any]] = []
        seen: set[tuple[str | None, int | None]] = set()

        # From validation
        validation_result = pipeline_run.stage_results.get(PipelineStage.VALIDATION)
        if validation_result:
            for signal_data in validation_result.output.get("signals", []):
                for finding in signal_data.get("findings", []):
                    fp = finding.get("file_path")
                    ln = finding.get("line_number")
                    if fp and (fp, ln) not in seen:
                        seen.add((fp, ln))
                        refs.append({
                            "file_path": fp,
                            "line_number": ln,
                            "source": f"validation.{signal_data['signal']}",
                            "context": finding.get("title", ""),
                        })

        # From sandbox test failures
        sandbox_result = pipeline_run.stage_results.get(PipelineStage.SANDBOX)
        if sandbox_result:
            test_results = sandbox_result.output.get("test_results", {})
            for failure in test_results.get("failures", []):
                structured = failure.get("structured_error", {})
                fp = structured.get("file")
                ln = structured.get("line")
                if fp and (fp, ln) not in seen:
                    seen.add((fp, ln))
                    refs.append({
                        "file_path": fp,
                        "line_number": ln,
                        "source": "sandbox.test_failure",
                        "context": failure.get("test_name", ""),
                    })

        return refs

    def _determine_next_actions(self, pipeline_run: PipelineRun) -> list[str]:
        """Produce an ordered list of recommended next steps for the agent."""
        actions: list[str] = []

        if pipeline_run.status == PipelineStatus.PASSED:
            actions.append("Pipeline succeeded. Monitor deployment for anomalies.")
            return actions

        if pipeline_run.status == PipelineStatus.BLOCKED:
            routing = pipeline_run.metadata.get("risk_assessment", {})
            route = routing.get("recommended_route", "unknown")
            if route == "human_approval":
                actions.append("Await human approval for deployment.")
            elif route == "human_approval_canary":
                actions.append(
                    "Await human approval for canary deployment (critical risk)."
                )
            else:
                actions.append(f"Pipeline blocked at deploy stage (route: {route}).")
            actions.append(
                "Consider reducing risk: limit scope, target fewer files, "
                "or build more trust via successful low-risk deployments."
            )
            return actions

        # Pipeline failed — guide the agent to fix it
        for stage, result in pipeline_run.stage_results.items():
            if result.status != PipelineStatus.FAILED:
                continue

            if stage == PipelineStage.INTENT:
                reasons = result.output.get("denial_reasons", [])
                for reason in reasons:
                    actions.append(f"Fix intent issue: {reason}")
                actions.append(
                    "Revise your intent declaration to comply with scope constraints, "
                    "then re-submit."
                )
            elif stage == PipelineStage.SANDBOX:
                actions.append("Fix failing tests identified in sandbox execution.")
                actions.append(
                    "Re-run the pipeline after applying fixes to the failing tests."
                )
            elif stage == PipelineStage.VALIDATION:
                recs = result.output.get("recommendations", [])
                for rec in recs:
                    actions.append(f"Address validation issue: {rec}")
                actions.append(
                    "Fix all blocking findings and re-submit for validation."
                )
            else:
                actions.append(f"Investigate failure in {stage.value} stage: {result.error}")

            # Only report the first failing stage since later stages didn't run
            break

        return actions
