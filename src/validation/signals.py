"""Validation signal runners for the Multi-Signal Validation Framework."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from .models import Finding, Severity, SignalResult, ValidationSignal


class ValidationSignalRunner(ABC):
    """Abstract base class for validation signal runners.

    Each runner executes one type of validation against the output of a
    sandboxed agent run and returns a :class:`SignalResult`.
    """

    @abstractmethod
    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        """Run this validation signal.

        Args:
            intent_id: The identifier of the intent being validated.
            sandbox_result: Arbitrary dict containing the sandbox execution
                output (files changed, test results, resource metrics, etc.).

        Returns:
            A ``SignalResult`` describing the outcome.
        """


# ---------------------------------------------------------------------------
# Concrete runners – each has a *simulate* mode so tests can control outcome
# ---------------------------------------------------------------------------


class StaticAnalysisRunner(ValidationSignalRunner):
    """Simulates running linters and type checkers against changed code."""

    def __init__(self, *, force_pass: Optional[bool] = None) -> None:
        self.force_pass = force_pass

    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        start = time.monotonic()
        findings: list[Finding] = []

        # Pull simulated lint issues from sandbox_result, if any
        lint_issues: list[dict[str, Any]] = sandbox_result.get("lint_issues", [])
        for issue in lint_issues:
            findings.append(
                Finding(
                    severity=Severity(issue.get("severity", "warning")),
                    title=issue.get("title", "Lint issue"),
                    description=issue.get("description", ""),
                    file_path=issue.get("file_path"),
                    line_number=issue.get("line_number"),
                    suggestion=issue.get("suggestion"),
                )
            )

        has_errors = any(
            f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings
        )

        if self.force_pass is not None:
            passed = self.force_pass
        else:
            passed = not has_errors

        confidence = 0.95 if passed else 0.85
        duration = time.monotonic() - start

        return SignalResult(
            signal=ValidationSignal.STATIC_ANALYSIS,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=duration,
        )


class BehavioralDiffRunner(ValidationSignalRunner):
    """Simulates comparing application behaviour before and after the change."""

    def __init__(self, *, force_pass: Optional[bool] = None) -> None:
        self.force_pass = force_pass

    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        start = time.monotonic()
        findings: list[Finding] = []

        behavioural_diffs: list[dict[str, Any]] = sandbox_result.get(
            "behavioral_diffs", []
        )
        for diff in behavioural_diffs:
            findings.append(
                Finding(
                    severity=Severity(diff.get("severity", "warning")),
                    title=diff.get("title", "Behavioural regression"),
                    description=diff.get("description", ""),
                    file_path=diff.get("file_path"),
                    line_number=diff.get("line_number"),
                    suggestion=diff.get("suggestion"),
                )
            )

        has_critical = any(
            f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings
        )

        if self.force_pass is not None:
            passed = self.force_pass
        else:
            passed = not has_critical

        confidence = 0.90 if passed else 0.80
        duration = time.monotonic() - start

        return SignalResult(
            signal=ValidationSignal.BEHAVIORAL_DIFF,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=duration,
        )


class IntentAlignmentRunner(ValidationSignalRunner):
    """Simulates checking whether the change matches the declared intent."""

    def __init__(self, *, force_pass: Optional[bool] = None) -> None:
        self.force_pass = force_pass

    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        start = time.monotonic()
        findings: list[Finding] = []

        alignment_issues: list[dict[str, Any]] = sandbox_result.get(
            "alignment_issues", []
        )
        for issue in alignment_issues:
            findings.append(
                Finding(
                    severity=Severity(issue.get("severity", "warning")),
                    title=issue.get("title", "Intent misalignment"),
                    description=issue.get("description", ""),
                    file_path=issue.get("file_path"),
                    line_number=issue.get("line_number"),
                    suggestion=issue.get("suggestion"),
                )
            )

        has_errors = any(
            f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings
        )

        if self.force_pass is not None:
            passed = self.force_pass
        else:
            passed = not has_errors

        confidence = 0.88 if passed else 0.75
        duration = time.monotonic() - start

        return SignalResult(
            signal=ValidationSignal.INTENT_ALIGNMENT,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=duration,
        )


class ResourceBoundsRunner(ValidationSignalRunner):
    """Checks whether resource usage exceeded configured thresholds."""

    def __init__(
        self,
        *,
        max_cpu_percent: float = 90.0,
        max_memory_mb: float = 1024.0,
        max_disk_mb: float = 512.0,
        force_pass: Optional[bool] = None,
    ) -> None:
        self.max_cpu_percent = max_cpu_percent
        self.max_memory_mb = max_memory_mb
        self.max_disk_mb = max_disk_mb
        self.force_pass = force_pass

    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        start = time.monotonic()
        findings: list[Finding] = []

        resources: dict[str, float] = sandbox_result.get("resource_usage", {})
        cpu = resources.get("cpu_percent", 0.0)
        memory = resources.get("memory_mb", 0.0)
        disk = resources.get("disk_mb", 0.0)

        breached = False

        if cpu > self.max_cpu_percent:
            breached = True
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    title="CPU usage exceeded threshold",
                    description=(
                        f"CPU usage {cpu:.1f}% exceeds limit {self.max_cpu_percent:.1f}%"
                    ),
                    suggestion=f"reduce_cpu_below:{self.max_cpu_percent}",
                )
            )

        if memory > self.max_memory_mb:
            breached = True
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    title="Memory usage exceeded threshold",
                    description=(
                        f"Memory usage {memory:.1f}MB exceeds limit "
                        f"{self.max_memory_mb:.1f}MB"
                    ),
                    suggestion=f"reduce_memory_below:{self.max_memory_mb}",
                )
            )

        if disk > self.max_disk_mb:
            breached = True
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    title="Disk usage exceeded threshold",
                    description=(
                        f"Disk usage {disk:.1f}MB exceeds limit "
                        f"{self.max_disk_mb:.1f}MB"
                    ),
                    suggestion=f"reduce_disk_below:{self.max_disk_mb}",
                )
            )

        if self.force_pass is not None:
            passed = self.force_pass
        else:
            passed = not breached

        confidence = 0.99 if passed else 0.95
        duration = time.monotonic() - start

        return SignalResult(
            signal=ValidationSignal.RESOURCE_BOUNDS,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=duration,
        )


class SecurityScanRunner(ValidationSignalRunner):
    """Simulates security scanning of agent-produced changes."""

    def __init__(self, *, force_pass: Optional[bool] = None) -> None:
        self.force_pass = force_pass

    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        start = time.monotonic()
        findings: list[Finding] = []

        security_issues: list[dict[str, Any]] = sandbox_result.get(
            "security_issues", []
        )
        for issue in security_issues:
            findings.append(
                Finding(
                    severity=Severity(issue.get("severity", "critical")),
                    title=issue.get("title", "Security vulnerability"),
                    description=issue.get("description", ""),
                    file_path=issue.get("file_path"),
                    line_number=issue.get("line_number"),
                    suggestion=issue.get("suggestion"),
                )
            )

        has_critical = any(
            f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings
        )

        if self.force_pass is not None:
            passed = self.force_pass
        else:
            passed = not has_critical

        confidence = 0.92 if passed else 0.88
        duration = time.monotonic() - start

        return SignalResult(
            signal=ValidationSignal.SECURITY_SCAN,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=duration,
        )
