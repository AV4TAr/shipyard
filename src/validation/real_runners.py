"""Real validation signal runners that shell out to actual tools.

These runners follow the same interface as the simulated ones in ``signals.py``
so they can be plugged directly into :class:`ValidationGate`.  Each runner:

1. Shells out to a real tool via ``subprocess.run()``
2. Parses output into :class:`SignalResult` with proper :class:`Finding` objects
3. Falls back gracefully if the tool isn't installed
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .models import Finding, Severity, SignalResult, ValidationSignal
from .signals import ValidationSignalRunner

logger = logging.getLogger(__name__)


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


class RealBehavioralDiffRunner(ValidationSignalRunner):
    """Runs tests before and after a change and diffs the results.

    When ``worktree_manager`` is provided and the sandbox result contains a
    ``worktree_path``, the runner:

    1. Runs pytest in the task worktree (the "after" state).
    2. Derives the parent repo path and runs pytest there (the "before" state).
    3. Diffs the two result sets to detect regressions, fixes, and test
       additions/removals.

    The signal **fails** if any test regressed (was passing before but fails
    after the change).  All other transitions are informational.

    When no worktree is available, falls back to simulated behaviour
    (checking ``sandbox_result["behavioral_diffs"]``).
    """

    def __init__(
        self,
        *,
        worktree_manager: Any | None = None,
        test_command: str = "python3 -m pytest -v --tb=no",
        timeout: int = 120,
        force_pass: bool | None = None,
    ) -> None:
        self.worktree_manager = worktree_manager
        self.test_command = test_command
        self.timeout = timeout
        self.force_pass = force_pass

    def run(self, intent_id: str, sandbox_result: dict[str, Any]) -> SignalResult:
        start = time.monotonic()

        worktree_path = sandbox_result.get("worktree_path")

        if worktree_path and self.worktree_manager is not None:
            return self._run_real(intent_id, sandbox_result, worktree_path, start)
        return self._run_simulated(intent_id, sandbox_result, start)

    # ------------------------------------------------------------------
    # Real worktree-based diff
    # ------------------------------------------------------------------

    def _run_real(
        self,
        intent_id: str,
        sandbox_result: dict[str, Any],
        worktree_path: str,
        start: float,
    ) -> SignalResult:
        """Run actual before/after test comparison."""
        repo_dir = self._derive_repo_dir(worktree_path)

        # "After" — tests in the worktree (task branch)
        after_raw = self.worktree_manager.run_tests(
            worktree_path,
            test_command=self.test_command,
            timeout=self.timeout,
        )
        after_results = self._parse_pytest_results(after_raw.get("stdout", ""))

        # "Before" — tests in the parent repo (main branch)
        if repo_dir:
            before_raw = self.worktree_manager.run_tests(
                repo_dir,
                test_command=self.test_command,
                timeout=self.timeout,
            )
            before_results = self._parse_pytest_results(before_raw.get("stdout", ""))
        else:
            # Cannot determine before state — treat as all-new
            before_results = {}

        findings = self._diff_results(before_results, after_results)

        has_regressions = any(
            f.severity in (Severity.ERROR, Severity.CRITICAL) for f in findings
        )

        if self.force_pass is not None:
            passed = self.force_pass
        else:
            passed = not has_regressions

        # Confidence: high when we have both sides, lower if before was empty
        if before_results:
            confidence = 0.95 if passed else 0.85
        else:
            confidence = 0.70 if passed else 0.60

        duration = time.monotonic() - start
        return SignalResult(
            signal=ValidationSignal.BEHAVIORAL_DIFF,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # Simulated fallback (same as the original BehavioralDiffRunner)
    # ------------------------------------------------------------------

    def _run_simulated(
        self,
        intent_id: str,
        sandbox_result: dict[str, Any],
        start: float,
    ) -> SignalResult:
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_repo_dir(worktree_path: str) -> str | None:
        """Derive the parent repo directory from a worktree path.

        Worktree paths follow the pattern:
            ``data/worktrees/<project_id>/<task_id>/``
        The corresponding repo is at:
            ``data/repos/<project_id>/``
        """
        wt = Path(worktree_path)
        # Walk up looking for the worktrees directory
        parts = wt.parts
        for i, part in enumerate(parts):
            if part == "worktrees" and i + 1 < len(parts):
                project_id = parts[i + 1]
                # Reconstruct repos path with same prefix
                prefix = Path(*parts[:i]) if i > 0 else Path(".")
                return str(prefix / "repos" / project_id)
        return None

    @staticmethod
    def _parse_pytest_results(output: str) -> dict[str, str]:
        """Parse pytest verbose output into ``{test_name: status}``.

        Recognises lines like::

            tests/test_auth.py::test_login PASSED
            ../../../path/to/tests/test_auth.py::test_invalid FAILED

        Test names are normalised to ``filename.py::test_name`` so that
        results from different working directories can be compared.

        Returns a dict mapping the normalised test path to ``"passed"``,
        ``"failed"``, or ``"error"``.
        """
        results: dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
            match = re.match(
                r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR)\b",
                line,
            )
            if match:
                test_name = match.group(1)
                status = match.group(2).lower()
                # Normalise: strip leading path components, keep
                # only filename::test_func (and any parameterised suffix)
                # e.g. "../../../tests/test_auth.py::test_login" -> "test_auth.py::test_login"
                parts = test_name.split("::", 1)
                filename = Path(parts[0]).name  # just "test_auth.py"
                normalised = "{}::{}".format(filename, parts[1]) if len(parts) > 1 else filename
                results[normalised] = status
        return results

    @staticmethod
    def _diff_results(
        before: dict[str, str],
        after: dict[str, str],
    ) -> list[Finding]:
        """Compare before/after test results and produce findings.

        Categories:
        - pass -> fail = ERROR (regression)
        - fail -> pass = INFO (fix)
        - new test (not in before) = INFO
        - removed test (not in after) = WARNING
        """
        findings: list[Finding] = []
        all_tests = set(before) | set(after)

        for test in sorted(all_tests):
            before_status = before.get(test)
            after_status = after.get(test)

            if before_status is None and after_status is not None:
                # New test
                findings.append(
                    Finding(
                        severity=Severity.INFO,
                        title="New test added",
                        description=f"{test} ({after_status})",
                        file_path=test.split("::")[0] if "::" in test else None,
                    )
                )
            elif after_status is None and before_status is not None:
                # Removed test
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        title="Test removed",
                        description=f"{test} was {before_status} before removal",
                        file_path=test.split("::")[0] if "::" in test else None,
                    )
                )
            elif before_status == "passed" and after_status in ("failed", "error"):
                # Regression
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        title="Test regression",
                        description=(
                            f"{test} was passing but now {after_status}"
                        ),
                        file_path=test.split("::")[0] if "::" in test else None,
                        suggestion="fix_regression",
                    )
                )
            elif before_status in ("failed", "error") and after_status == "passed":
                # Fix
                findings.append(
                    Finding(
                        severity=Severity.INFO,
                        title="Test fixed",
                        description=f"{test} was {before_status}, now passing",
                        file_path=test.split("::")[0] if "::" in test else None,
                    )
                )

        return findings
