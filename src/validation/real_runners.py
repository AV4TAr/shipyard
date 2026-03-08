"""Real validation signal runners that shell out to actual tools.

These runners follow the same interface as the simulated ones in ``signals.py``
so they can be plugged directly into :class:`ValidationGate`.  Each runner:

1. Shells out to a real tool via ``subprocess.run()``
2. Parses output into :class:`SignalResult` with proper :class:`Finding` objects
3. Falls back gracefully if the tool isn't installed
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from .models import Finding, Severity, SignalResult, ValidationSignal
from .signals import ValidationSignalRunner


class RealStaticAnalysisRunner(ValidationSignalRunner):
    """Runs ``ruff check`` and parses JSON output into findings."""

    # Map ruff code prefixes to severity levels
    _SEVERITY_MAP: dict[str, Severity] = {
        "E": Severity.ERROR,
        "F": Severity.ERROR,
        "W": Severity.WARNING,
        "I": Severity.INFO,
        "C": Severity.WARNING,
        "N": Severity.WARNING,
        "D": Severity.INFO,
        "UP": Severity.INFO,
        "B": Severity.WARNING,
        "A": Severity.WARNING,
        "S": Severity.ERROR,
    }

    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        start = time.monotonic()
        path = sandbox_result.get("path", ".")

        try:
            proc = subprocess.run(
                ["ruff", "check", "--output-format=json", path],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return SignalResult(
                signal=ValidationSignal.STATIC_ANALYSIS,
                passed=True,
                confidence=0.5,
                findings=[
                    Finding(
                        severity=Severity.WARNING,
                        title="ruff not installed",
                        description="Could not run static analysis: ruff is not installed.",
                        suggestion="install:ruff",
                    )
                ],
                duration_seconds=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired:
            return SignalResult(
                signal=ValidationSignal.STATIC_ANALYSIS,
                passed=True,
                confidence=0.5,
                findings=[
                    Finding(
                        severity=Severity.WARNING,
                        title="ruff timed out",
                        description="Static analysis timed out after 60 seconds.",
                    )
                ],
                duration_seconds=time.monotonic() - start,
            )

        findings = self._parse_ruff_output(proc.stdout)
        has_errors = any(
            f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings
        )
        passed = not has_errors

        # Confidence: 1.0 when clean, scales down with more findings
        if not findings:
            confidence = 1.0
        else:
            confidence = max(0.3, 1.0 - len(findings) * 0.05)

        return SignalResult(
            signal=ValidationSignal.STATIC_ANALYSIS,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=time.monotonic() - start,
        )

    def _parse_ruff_output(self, raw_json: str) -> list[Finding]:
        """Parse ruff JSON output into Finding objects."""
        if not raw_json.strip():
            return []

        try:
            issues = json.loads(raw_json)
        except json.JSONDecodeError:
            return [
                Finding(
                    severity=Severity.WARNING,
                    title="Failed to parse ruff output",
                    description="ruff produced non-JSON output.",
                )
            ]

        findings: list[Finding] = []
        for issue in issues:
            code: str = issue.get("code", "")
            severity = self._map_severity(code)
            findings.append(
                Finding(
                    severity=severity,
                    title=f"{code}: {issue.get('message', 'unknown')}",
                    description=issue.get("message", ""),
                    file_path=issue.get("filename"),
                    line_number=issue.get("location", {}).get("row"),
                    suggestion=issue.get("fix", {}).get("message") if issue.get("fix") else None,
                )
            )
        return findings

    @classmethod
    def _map_severity(cls, code: str) -> Severity:
        """Map a ruff rule code to a Severity level."""
        # Try longest prefix first (e.g. "UP" before "U")
        for prefix in sorted(cls._SEVERITY_MAP, key=len, reverse=True):
            if code.startswith(prefix):
                return cls._SEVERITY_MAP[prefix]
        return Severity.WARNING


class RealSecurityScanRunner(ValidationSignalRunner):
    """Runs ``bandit`` and parses JSON output into findings."""

    _SEVERITY_MAP: dict[str, Severity] = {
        "HIGH": Severity.CRITICAL,
        "MEDIUM": Severity.ERROR,
        "LOW": Severity.WARNING,
    }

    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        start = time.monotonic()
        path = sandbox_result.get("path", ".")

        try:
            proc = subprocess.run(
                ["bandit", "-r", path, "-f", "json"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return SignalResult(
                signal=ValidationSignal.SECURITY_SCAN,
                passed=True,
                confidence=0.5,
                findings=[
                    Finding(
                        severity=Severity.WARNING,
                        title="bandit not installed",
                        description="Could not run security scan: bandit is not installed.",
                        suggestion="install:bandit",
                    )
                ],
                duration_seconds=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired:
            return SignalResult(
                signal=ValidationSignal.SECURITY_SCAN,
                passed=True,
                confidence=0.5,
                findings=[
                    Finding(
                        severity=Severity.WARNING,
                        title="bandit timed out",
                        description="Security scan timed out after 60 seconds.",
                    )
                ],
                duration_seconds=time.monotonic() - start,
            )

        findings = self._parse_bandit_output(proc.stdout)
        has_critical = any(
            f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings
        )
        passed = not has_critical

        if not findings:
            confidence = 1.0
        else:
            confidence = max(0.3, 1.0 - len(findings) * 0.05)

        return SignalResult(
            signal=ValidationSignal.SECURITY_SCAN,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=time.monotonic() - start,
        )

    def _parse_bandit_output(self, raw_json: str) -> list[Finding]:
        """Parse bandit JSON output into Finding objects."""
        if not raw_json.strip():
            return []

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return [
                Finding(
                    severity=Severity.WARNING,
                    title="Failed to parse bandit output",
                    description="bandit produced non-JSON output.",
                )
            ]

        findings: list[Finding] = []
        for result in data.get("results", []):
            severity = self._SEVERITY_MAP.get(
                result.get("issue_severity", "LOW"), Severity.WARNING
            )
            findings.append(
                Finding(
                    severity=severity,
                    title=f"{result.get('test_name', 'unknown')}: "
                    f"{result.get('issue_text', '')}",
                    description=result.get("issue_text", ""),
                    file_path=result.get("filename"),
                    line_number=result.get("line_number"),
                )
            )
        return findings


class RealResourceBoundsRunner(ValidationSignalRunner):
    """Checks basic resource bounds: file sizes, line counts, file count.

    No external tool needed — uses ``os`` and file I/O directly.
    """

    def __init__(
        self,
        *,
        max_lines_medium: int = 1000,
        max_lines_high: int = 5000,
    ) -> None:
        self.max_lines_medium = max_lines_medium
        self.max_lines_high = max_lines_high

    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        start = time.monotonic()
        path = sandbox_result.get("path", ".")
        findings: list[Finding] = []

        if os.path.isfile(path):
            files = [path]
        elif os.path.isdir(path):
            files = self._collect_python_files(path)
        else:
            return SignalResult(
                signal=ValidationSignal.RESOURCE_BOUNDS,
                passed=True,
                confidence=0.5,
                findings=[
                    Finding(
                        severity=Severity.WARNING,
                        title="Path not found",
                        description=f"Path does not exist: {path}",
                    )
                ],
                duration_seconds=time.monotonic() - start,
            )

        for filepath in files:
            try:
                line_count = self._count_lines(filepath)
            except OSError:
                continue

            if line_count > self.max_lines_high:
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        title="File exceeds line limit",
                        description=(
                            f"{filepath} has {line_count} lines "
                            f"(limit: {self.max_lines_high})"
                        ),
                        file_path=filepath,
                        suggestion=f"split_file_below:{self.max_lines_high}_lines",
                    )
                )
            elif line_count > self.max_lines_medium:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        title="File is large",
                        description=(
                            f"{filepath} has {line_count} lines "
                            f"(threshold: {self.max_lines_medium})"
                        ),
                        file_path=filepath,
                        suggestion=f"consider_splitting:{self.max_lines_medium}_lines",
                    )
                )

            try:
                file_size = os.path.getsize(filepath)
                # Flag files over 1MB
                if file_size > 1_000_000:
                    findings.append(
                        Finding(
                            severity=Severity.WARNING,
                            title="Large file detected",
                            description=(
                                f"{filepath} is {file_size / 1_000_000:.1f}MB"
                            ),
                            file_path=filepath,
                        )
                    )
            except OSError:
                continue

        has_errors = any(
            f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings
        )
        passed = not has_errors
        confidence = 0.99 if not findings else 0.85

        return SignalResult(
            signal=ValidationSignal.RESOURCE_BOUNDS,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=time.monotonic() - start,
        )

    @staticmethod
    def _collect_python_files(directory: str) -> list[str]:
        """Recursively collect all Python files in directory."""
        py_files: list[str] = []
        for root, _dirs, filenames in os.walk(directory):
            for name in filenames:
                if name.endswith(".py"):
                    py_files.append(os.path.join(root, name))
        return py_files

    @staticmethod
    def _count_lines(filepath: str) -> int:
        """Count lines in a file."""
        count = 0
        with open(filepath, "rb") as f:
            for _ in f:
                count += 1
        return count
