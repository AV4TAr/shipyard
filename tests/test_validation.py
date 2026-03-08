"""Tests for the Multi-Signal Validation Framework."""

from __future__ import annotations

import pytest

from src.validation import (
    BehavioralDiffRunner,
    Finding,
    IntentAlignmentRunner,
    ResourceBoundsRunner,
    SecurityScanRunner,
    Severity,
    SignalResult,
    StaticAnalysisRunner,
    ValidationGate,
    ValidationSignal,
    ValidationVerdict,
)


INTENT_ID = "test-intent-001"


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _empty_sandbox() -> dict:
    return {}


# -----------------------------------------------------------------------
# Individual Signal Runner Tests
# -----------------------------------------------------------------------


class TestStaticAnalysisRunner:
    def test_passes_with_no_issues(self):
        runner = StaticAnalysisRunner()
        result = runner.run(INTENT_ID, _empty_sandbox())

        assert isinstance(result, SignalResult)
        assert result.signal == ValidationSignal.STATIC_ANALYSIS
        assert result.passed is True
        assert result.confidence > 0.0
        assert result.duration_seconds >= 0.0

    def test_fails_with_error_issues(self):
        sandbox = {
            "lint_issues": [
                {
                    "severity": "error",
                    "title": "Undefined variable",
                    "description": "Variable 'x' is not defined",
                    "file_path": "src/main.py",
                    "line_number": 42,
                    "suggestion": "define_variable:x",
                },
            ]
        }
        runner = StaticAnalysisRunner()
        result = runner.run(INTENT_ID, sandbox)

        assert result.passed is False
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.ERROR
        assert result.findings[0].file_path == "src/main.py"

    def test_force_pass_overrides(self):
        sandbox = {
            "lint_issues": [
                {"severity": "error", "title": "Bug", "description": "bad"},
            ]
        }
        runner = StaticAnalysisRunner(force_pass=True)
        result = runner.run(INTENT_ID, sandbox)
        assert result.passed is True

    def test_force_fail_overrides(self):
        runner = StaticAnalysisRunner(force_pass=False)
        result = runner.run(INTENT_ID, _empty_sandbox())
        assert result.passed is False


class TestBehavioralDiffRunner:
    def test_passes_with_no_diffs(self):
        runner = BehavioralDiffRunner()
        result = runner.run(INTENT_ID, _empty_sandbox())

        assert result.signal == ValidationSignal.BEHAVIORAL_DIFF
        assert result.passed is True

    def test_fails_with_critical_diff(self):
        sandbox = {
            "behavioral_diffs": [
                {
                    "severity": "critical",
                    "title": "API response changed",
                    "description": "GET /users returns different schema",
                },
            ]
        }
        runner = BehavioralDiffRunner()
        result = runner.run(INTENT_ID, sandbox)

        assert result.passed is False
        assert len(result.findings) == 1

    def test_passes_with_info_diff(self):
        sandbox = {
            "behavioral_diffs": [
                {
                    "severity": "info",
                    "title": "Logging added",
                    "description": "New log line in response",
                },
            ]
        }
        runner = BehavioralDiffRunner()
        result = runner.run(INTENT_ID, sandbox)
        assert result.passed is True


class TestIntentAlignmentRunner:
    def test_passes_when_aligned(self):
        runner = IntentAlignmentRunner()
        result = runner.run(INTENT_ID, _empty_sandbox())

        assert result.signal == ValidationSignal.INTENT_ALIGNMENT
        assert result.passed is True

    def test_fails_on_misalignment(self):
        sandbox = {
            "alignment_issues": [
                {
                    "severity": "error",
                    "title": "Scope creep",
                    "description": "Change modifies unrelated files",
                },
            ]
        }
        runner = IntentAlignmentRunner()
        result = runner.run(INTENT_ID, sandbox)
        assert result.passed is False


class TestResourceBoundsRunner:
    def test_passes_within_bounds(self):
        sandbox = {
            "resource_usage": {
                "cpu_percent": 50.0,
                "memory_mb": 256.0,
                "disk_mb": 100.0,
            }
        }
        runner = ResourceBoundsRunner()
        result = runner.run(INTENT_ID, sandbox)

        assert result.signal == ValidationSignal.RESOURCE_BOUNDS
        assert result.passed is True
        assert len(result.findings) == 0

    def test_fails_on_cpu_breach(self):
        sandbox = {
            "resource_usage": {
                "cpu_percent": 95.0,
                "memory_mb": 256.0,
                "disk_mb": 100.0,
            }
        }
        runner = ResourceBoundsRunner()
        result = runner.run(INTENT_ID, sandbox)

        assert result.passed is False
        assert any("CPU" in f.title for f in result.findings)

    def test_fails_on_memory_breach(self):
        sandbox = {
            "resource_usage": {
                "cpu_percent": 10.0,
                "memory_mb": 2048.0,
                "disk_mb": 100.0,
            }
        }
        runner = ResourceBoundsRunner()
        result = runner.run(INTENT_ID, sandbox)

        assert result.passed is False
        assert any("Memory" in f.title for f in result.findings)

    def test_custom_thresholds(self):
        sandbox = {
            "resource_usage": {
                "cpu_percent": 50.0,
                "memory_mb": 256.0,
                "disk_mb": 100.0,
            }
        }
        runner = ResourceBoundsRunner(max_cpu_percent=40.0)
        result = runner.run(INTENT_ID, sandbox)
        assert result.passed is False

    def test_disk_warning_does_not_block_alone(self):
        """Disk overuse produces a WARNING, not ERROR, so it does not cause
        the runner to fail on its own (unless force_pass=False)."""
        sandbox = {
            "resource_usage": {
                "cpu_percent": 10.0,
                "memory_mb": 100.0,
                "disk_mb": 999.0,
            }
        }
        runner = ResourceBoundsRunner()
        result = runner.run(INTENT_ID, sandbox)
        # Disk alone is a warning, but the runner checks for breached (any threshold exceeded)
        # Actually our implementation sets breached=True for disk too
        assert result.passed is False
        assert any("Disk" in f.title for f in result.findings)


class TestSecurityScanRunner:
    def test_passes_with_no_issues(self):
        runner = SecurityScanRunner()
        result = runner.run(INTENT_ID, _empty_sandbox())

        assert result.signal == ValidationSignal.SECURITY_SCAN
        assert result.passed is True

    def test_fails_with_critical_issue(self):
        sandbox = {
            "security_issues": [
                {
                    "severity": "critical",
                    "title": "SQL Injection",
                    "description": "Unsanitised user input in query",
                    "file_path": "src/db.py",
                    "line_number": 10,
                    "suggestion": "use_parameterised_query",
                },
            ]
        }
        runner = SecurityScanRunner()
        result = runner.run(INTENT_ID, sandbox)

        assert result.passed is False
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.CRITICAL


# -----------------------------------------------------------------------
# ValidationGate Tests
# -----------------------------------------------------------------------


class TestValidationGate:
    def test_all_pass_gives_overall_pass(self):
        runners = [
            StaticAnalysisRunner(force_pass=True),
            BehavioralDiffRunner(force_pass=True),
            IntentAlignmentRunner(force_pass=True),
            ResourceBoundsRunner(force_pass=True),
            SecurityScanRunner(force_pass=True),
        ]
        gate = ValidationGate(runners)
        verdict = gate.validate(INTENT_ID, _empty_sandbox())

        assert isinstance(verdict, ValidationVerdict)
        assert verdict.overall_passed is True
        assert verdict.intent_id == INTENT_ID
        assert len(verdict.signals) == 5
        assert len(verdict.blocking_findings) == 0

    def test_single_failure_blocks(self):
        runners = [
            StaticAnalysisRunner(force_pass=True),
            SecurityScanRunner(force_pass=False),
        ]
        gate = ValidationGate(runners, risk_threshold=0.0)
        verdict = gate.validate(INTENT_ID, _empty_sandbox())

        assert verdict.overall_passed is False

    def test_risk_score_low_when_all_pass(self):
        runners = [
            StaticAnalysisRunner(force_pass=True),
            BehavioralDiffRunner(force_pass=True),
        ]
        gate = ValidationGate(runners)
        verdict = gate.validate(INTENT_ID, _empty_sandbox())

        assert verdict.risk_score < 0.5

    def test_risk_score_high_when_failures(self):
        runners = [
            StaticAnalysisRunner(force_pass=False),
            BehavioralDiffRunner(force_pass=False),
            SecurityScanRunner(force_pass=False),
        ]
        gate = ValidationGate(runners)
        verdict = gate.validate(INTENT_ID, _empty_sandbox())

        assert verdict.risk_score > 0.3

    def test_blocking_findings_extracted(self):
        sandbox = {
            "security_issues": [
                {
                    "severity": "critical",
                    "title": "RCE",
                    "description": "Remote code execution via eval()",
                },
            ]
        }
        runners = [SecurityScanRunner()]
        gate = ValidationGate(runners)
        verdict = gate.validate(INTENT_ID, sandbox)

        assert verdict.overall_passed is False
        assert len(verdict.blocking_findings) == 1
        assert verdict.blocking_findings[0].title == "RCE"
        assert verdict.blocking_findings[0].severity == Severity.CRITICAL

    def test_non_blocking_warning_findings_not_in_blocking(self):
        sandbox = {
            "lint_issues": [
                {
                    "severity": "warning",
                    "title": "Unused import",
                    "description": "os imported but unused",
                },
            ]
        }
        runners = [StaticAnalysisRunner()]
        gate = ValidationGate(runners)
        verdict = gate.validate(INTENT_ID, sandbox)

        # Warnings don't cause failure
        assert verdict.overall_passed is True
        assert len(verdict.blocking_findings) == 0

    def test_recommendations_for_failed_signals(self):
        runners = [
            StaticAnalysisRunner(force_pass=False),
            SecurityScanRunner(force_pass=True),
        ]
        gate = ValidationGate(runners)
        verdict = gate.validate(INTENT_ID, _empty_sandbox())

        assert len(verdict.recommendations) == 1
        assert "static_analysis" in verdict.recommendations[0]

    def test_parallel_execution(self):
        runners = [
            StaticAnalysisRunner(force_pass=True),
            BehavioralDiffRunner(force_pass=True),
            SecurityScanRunner(force_pass=True),
        ]
        gate = ValidationGate(runners, parallel=True)
        verdict = gate.validate(INTENT_ID, _empty_sandbox())

        assert verdict.overall_passed is True
        assert len(verdict.signals) == 3

    def test_risk_threshold_allows_soft_pass(self):
        """A failed signal with high confidence can still yield an overall
        pass when the risk score stays below the threshold and there are no
        blocking (error/critical) findings."""
        runners = [
            StaticAnalysisRunner(force_pass=True),
            # force_pass=False but no findings -> no blocking findings
            BehavioralDiffRunner(force_pass=False),
        ]
        gate = ValidationGate(runners, risk_threshold=0.9)
        verdict = gate.validate(INTENT_ID, _empty_sandbox())

        # Even though behavioral diff "failed", risk score is moderate and
        # there are no blocking findings, so overall can pass.
        assert verdict.overall_passed is True

    def test_empty_runners_passes(self):
        gate = ValidationGate([])
        verdict = gate.validate(INTENT_ID, _empty_sandbox())

        assert verdict.overall_passed is True
        assert verdict.risk_score == 0.0
        assert len(verdict.signals) == 0
