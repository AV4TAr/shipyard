"""Validation gate that aggregates multiple signal runners into a verdict."""

from __future__ import annotations

import concurrent.futures
from typing import Any, Dict, List, Optional

from .models import (
    Finding,
    Severity,
    SignalResult,
    ValidationVerdict,
)
from .signals import ValidationSignalRunner


# Default weights per signal type (keyed by ValidationSignal value)
_DEFAULT_WEIGHTS: dict[str, float] = {
    "static_analysis": 1.0,
    "behavioral_diff": 1.5,
    "intent_alignment": 1.2,
    "resource_bounds": 1.0,
    "security_scan": 2.0,
}


class ValidationGate:
    """Runs all validation signal runners and produces a :class:`ValidationVerdict`.

    Args:
        runners: The signal runners to execute.
        risk_threshold: Maximum acceptable risk score.  If the computed risk
            score is strictly below this threshold *and* no individual signal
            has blocking findings, the change may still pass even if not every
            signal reports ``passed=True``.
        weights: Optional mapping of ``ValidationSignal.value`` to a weight
            used when computing the risk score.  Defaults to
            ``_DEFAULT_WEIGHTS``.
        parallel: Whether to run signals concurrently.
    """

    def __init__(
        self,
        runners: list[ValidationSignalRunner],
        *,
        risk_threshold: float = 0.5,
        weights: Optional[Dict[str, float]] = None,
        parallel: bool = False,
    ) -> None:
        self.runners = runners
        self.risk_threshold = risk_threshold
        self.weights = weights or _DEFAULT_WEIGHTS
        self.parallel = parallel

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self, intent_id: str, sandbox_result: dict[str, Any]
    ) -> ValidationVerdict:
        """Run every registered signal runner and aggregate their results."""

        if self.parallel:
            results = self._run_parallel(intent_id, sandbox_result)
        else:
            results = self._run_sequential(intent_id, sandbox_result)

        blocking = self._extract_blocking_findings(results)
        risk_score = self._compute_risk_score(results)
        all_passed = all(r.passed for r in results)
        overall_passed = all_passed or (risk_score < self.risk_threshold and not blocking)
        recommendations = self._build_recommendations(results)

        return ValidationVerdict(
            intent_id=intent_id,
            signals=results,
            overall_passed=overall_passed,
            risk_score=risk_score,
            blocking_findings=blocking,
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_sequential(
        self, intent_id: str, sandbox_result: dict[str, Any]
    ) -> list[SignalResult]:
        return [runner.run(intent_id, sandbox_result) for runner in self.runners]

    def _run_parallel(
        self, intent_id: str, sandbox_result: dict[str, Any]
    ) -> list[SignalResult]:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(runner.run, intent_id, sandbox_result): runner
                for runner in self.runners
            }
            results: list[SignalResult] = []
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
        return results

    def _compute_risk_score(self, results: list[SignalResult]) -> float:
        """Weighted average of ``(1 - confidence)`` for failed signals and
        ``(1 - confidence) * 0.2`` for passing signals.

        The idea: a failed signal contributes its full uncertainty to risk,
        while a passing signal contributes only a fraction.
        """
        if not results:
            return 0.0

        total_weight = 0.0
        weighted_risk = 0.0

        for result in results:
            w = self.weights.get(result.signal.value, 1.0)
            total_weight += w
            uncertainty = 1.0 - result.confidence
            if result.passed:
                weighted_risk += w * uncertainty * 0.2
            else:
                weighted_risk += w * (0.5 + uncertainty * 0.5)

        score = weighted_risk / total_weight if total_weight else 0.0
        return min(max(score, 0.0), 1.0)

    @staticmethod
    def _extract_blocking_findings(results: list[SignalResult]) -> list[Finding]:
        """Return findings of severity ERROR or CRITICAL from failed signals."""
        blocking: list[Finding] = []
        for result in results:
            if not result.passed:
                for finding in result.findings:
                    if finding.severity in (Severity.ERROR, Severity.CRITICAL):
                        blocking.append(finding)
        return blocking

    @staticmethod
    def _build_recommendations(results: list[SignalResult]) -> list[str]:
        recommendations: list[str] = []
        for result in results:
            if not result.passed:
                recommendations.append(
                    f"Fix issues reported by {result.signal.value} "
                    f"({len(result.findings)} finding(s))"
                )
        return recommendations
