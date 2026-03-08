"""Tests for real validation signal runners."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from src.validation.models import Severity, ValidationSignal
from src.validation.real_runners import (
    RealResourceBoundsRunner,
    RealSecurityScanRunner,
    RealStaticAnalysisRunner,
)

# ---------------------------------------------------------------------------
# RealStaticAnalysisRunner
# ---------------------------------------------------------------------------


class TestRealStaticAnalysisRunner:
    """Tests for the ruff-backed static analysis runner."""

    def test_run_against_src_directory(self):
        """Run ruff against the actual src/ directory (ruff should be installed)."""
        runner = RealStaticAnalysisRunner()
        result = runner.run("test-intent", {"path": "src/"})

        assert result.signal == ValidationSignal.STATIC_ANALYSIS
        assert 0.0 <= result.confidence <= 1.0
        assert result.duration_seconds >= 0.0
        # ruff is installed, so we should get a real result (not the fallback)
        if result.findings:
            # If there are findings, they should have proper structure
            for f in result.findings:
                assert f.severity in list(Severity)
                assert f.title

    def test_parse_ruff_json_output(self):
        """Test parsing of ruff JSON output with known data."""
        runner = RealStaticAnalysisRunner()
        sample_output = json.dumps([
            {
                "code": "E501",
                "message": "Line too long (120 > 100)",
                "filename": "src/foo.py",
                "location": {"row": 42, "column": 1},
                "fix": None,
            },
            {
                "code": "W291",
                "message": "Trailing whitespace",
                "filename": "src/bar.py",
                "location": {"row": 10, "column": 5},
                "fix": {"message": "Remove trailing whitespace"},
            },
            {
                "code": "F401",
                "message": "os imported but unused",
                "filename": "src/baz.py",
                "location": {"row": 1, "column": 1},
                "fix": {"message": "Remove unused import"},
            },
            {
                "code": "I001",
                "message": "Import block is unsorted",
                "filename": "src/baz.py",
                "location": {"row": 1, "column": 1},
                "fix": None,
            },
        ])

        findings = runner._parse_ruff_output(sample_output)

        assert len(findings) == 4

        # E501 -> ERROR (HIGH)
        assert findings[0].severity == Severity.ERROR
        assert "E501" in findings[0].title
        assert findings[0].file_path == "src/foo.py"
        assert findings[0].line_number == 42

        # W291 -> WARNING (MEDIUM)
        assert findings[1].severity == Severity.WARNING
        assert findings[1].suggestion == "Remove trailing whitespace"

        # F401 -> ERROR (HIGH)
        assert findings[2].severity == Severity.ERROR

        # I001 -> INFO (LOW)
        assert findings[3].severity == Severity.INFO

    def test_parse_empty_output(self):
        """No findings when ruff output is empty."""
        runner = RealStaticAnalysisRunner()
        findings = runner._parse_ruff_output("")
        assert findings == []

    def test_parse_invalid_json(self):
        """Graceful handling of non-JSON output."""
        runner = RealStaticAnalysisRunner()
        findings = runner._parse_ruff_output("not json at all")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "parse" in findings[0].title.lower()

    def test_confidence_scales_with_findings(self):
        """More findings should reduce confidence."""
        runner = RealStaticAnalysisRunner()
        many_issues = json.dumps([
            {
                "code": "W291",
                "message": f"Issue {i}",
                "filename": "f.py",
                "location": {"row": i, "column": 1},
                "fix": None,
            }
            for i in range(20)
        ])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout=many_issues, stderr=""
            )
            result = runner.run("intent-1", {"path": "."})

        assert result.confidence < 1.0
        # With 20 findings: max(0.3, 1.0 - 20*0.05) = 0.3
        assert result.confidence == pytest.approx(0.3, abs=0.01)

    def test_fallback_when_ruff_not_installed(self):
        """Should return a warning finding, not crash."""
        runner = RealStaticAnalysisRunner()

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = runner.run("intent-1", {"path": "."})

        assert result.signal == ValidationSignal.STATIC_ANALYSIS
        assert result.passed is True
        assert result.confidence == 0.5
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.WARNING
        assert "not installed" in result.findings[0].description

    def test_timeout_handling(self):
        """Should handle subprocess timeout gracefully."""
        runner = RealStaticAnalysisRunner()

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ruff", timeout=60),
        ):
            result = runner.run("intent-1", {"path": "."})

        assert result.passed is True
        assert result.confidence == 0.5
        assert len(result.findings) == 1
        assert "timed out" in result.findings[0].title

    def test_clean_code_returns_high_confidence(self):
        """No findings should give confidence of 1.0."""
        runner = RealStaticAnalysisRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="[]", stderr=""
            )
            result = runner.run("intent-1", {"path": "."})

        assert result.passed is True
        assert result.confidence == 1.0
        assert result.findings == []


# ---------------------------------------------------------------------------
# RealSecurityScanRunner
# ---------------------------------------------------------------------------


class TestRealSecurityScanRunner:
    """Tests for the bandit-backed security scan runner."""

    def test_parse_bandit_json_output(self):
        """Test parsing of bandit JSON output with known data."""
        runner = RealSecurityScanRunner()
        sample = json.dumps({
            "results": [
                {
                    "test_name": "hardcoded_password_string",
                    "issue_text": "Possible hardcoded password: 'secret123'",
                    "issue_severity": "HIGH",
                    "issue_confidence": "MEDIUM",
                    "filename": "src/config.py",
                    "line_number": 15,
                },
                {
                    "test_name": "try_except_pass",
                    "issue_text": "Try, Except, Pass detected.",
                    "issue_severity": "LOW",
                    "issue_confidence": "HIGH",
                    "filename": "src/utils.py",
                    "line_number": 42,
                },
            ],
            "metrics": {},
        })

        findings = runner._parse_bandit_output(sample)

        assert len(findings) == 2

        # HIGH -> CRITICAL
        assert findings[0].severity == Severity.CRITICAL
        assert "hardcoded_password" in findings[0].title
        assert findings[0].file_path == "src/config.py"
        assert findings[0].line_number == 15

        # LOW -> WARNING
        assert findings[1].severity == Severity.WARNING

    def test_parse_empty_results(self):
        """No findings when bandit finds nothing."""
        runner = RealSecurityScanRunner()
        findings = runner._parse_bandit_output(json.dumps({"results": []}))
        assert findings == []

    def test_parse_empty_output(self):
        """Handle empty string output."""
        runner = RealSecurityScanRunner()
        findings = runner._parse_bandit_output("")
        assert findings == []

    def test_parse_invalid_json(self):
        """Graceful handling of non-JSON output."""
        runner = RealSecurityScanRunner()
        findings = runner._parse_bandit_output("error: something broke")
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_fallback_when_bandit_not_installed(self):
        """Should return a warning finding, not crash."""
        runner = RealSecurityScanRunner()

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = runner.run("intent-1", {"path": "."})

        assert result.signal == ValidationSignal.SECURITY_SCAN
        assert result.passed is True
        assert result.confidence == 0.5
        assert len(result.findings) == 1
        assert "not installed" in result.findings[0].description

    def test_timeout_handling(self):
        """Should handle subprocess timeout gracefully."""
        runner = RealSecurityScanRunner()

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="bandit", timeout=60),
        ):
            result = runner.run("intent-1", {"path": "."})

        assert result.passed is True
        assert result.confidence == 0.5
        assert "timed out" in result.findings[0].title

    def test_critical_findings_cause_failure(self):
        """HIGH-severity bandit issues (mapped to CRITICAL) should fail the signal."""
        runner = RealSecurityScanRunner()
        sample = json.dumps({
            "results": [
                {
                    "test_name": "exec_used",
                    "issue_text": "Use of exec detected.",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "filename": "evil.py",
                    "line_number": 1,
                },
            ],
        })

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout=sample, stderr=""
            )
            result = runner.run("intent-1", {"path": "."})

        assert result.passed is False
        assert result.findings[0].severity == Severity.CRITICAL

    def test_medium_severity_mapped_to_error(self):
        """MEDIUM bandit severity should map to ERROR."""
        runner = RealSecurityScanRunner()
        sample = json.dumps({
            "results": [
                {
                    "test_name": "request_without_timeout",
                    "issue_text": "Requests call without timeout.",
                    "issue_severity": "MEDIUM",
                    "issue_confidence": "HIGH",
                    "filename": "client.py",
                    "line_number": 5,
                },
            ],
        })

        findings = runner._parse_bandit_output(sample)
        assert findings[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# RealResourceBoundsRunner
# ---------------------------------------------------------------------------


class TestRealResourceBoundsRunner:
    """Tests for the file-based resource bounds checker."""

    def test_small_files_pass(self):
        """Files under the threshold should pass cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a small file (100 lines)
            small_file = os.path.join(tmpdir, "small.py")
            with open(small_file, "w") as f:
                for i in range(100):
                    f.write(f"line {i}\n")

            runner = RealResourceBoundsRunner()
            result = runner.run("intent-1", {"path": tmpdir})

        assert result.signal == ValidationSignal.RESOURCE_BOUNDS
        assert result.passed is True
        assert result.confidence == 0.99
        assert result.findings == []

    def test_medium_file_warning(self):
        """Files over 1000 lines should produce a WARNING finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            med_file = os.path.join(tmpdir, "medium.py")
            with open(med_file, "w") as f:
                for i in range(1500):
                    f.write(f"line {i}\n")

            runner = RealResourceBoundsRunner()
            result = runner.run("intent-1", {"path": tmpdir})

        assert result.passed is True  # warnings don't cause failure
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.WARNING
        assert "1500" in result.findings[0].description

    def test_large_file_error(self):
        """Files over 5000 lines should produce an ERROR finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            big_file = os.path.join(tmpdir, "big.py")
            with open(big_file, "w") as f:
                for i in range(5500):
                    f.write(f"line {i}\n")

            runner = RealResourceBoundsRunner()
            result = runner.run("intent-1", {"path": tmpdir})

        assert result.passed is False
        assert any(f.severity == Severity.ERROR for f in result.findings)
        assert "5500" in result.findings[0].description

    def test_single_file_path(self):
        """Runner works when given a single file instead of directory."""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            for i in range(50):
                f.write(f"x = {i}\n")
            filepath = f.name

        try:
            runner = RealResourceBoundsRunner()
            result = runner.run("intent-1", {"path": filepath})
            assert result.passed is True
        finally:
            os.unlink(filepath)

    def test_nonexistent_path(self):
        """Should handle nonexistent paths gracefully."""
        runner = RealResourceBoundsRunner()
        result = runner.run("intent-1", {"path": "/nonexistent/path/xyz"})

        assert result.passed is True
        assert result.confidence == 0.5
        assert len(result.findings) == 1
        assert "not found" in result.findings[0].title.lower() or \
               "not exist" in result.findings[0].description.lower()

    def test_custom_thresholds(self):
        """Custom thresholds should be respected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f_path = os.path.join(tmpdir, "mod.py")
            with open(f_path, "w") as f:
                for i in range(200):
                    f.write(f"line {i}\n")

            runner = RealResourceBoundsRunner(
                max_lines_medium=100, max_lines_high=300
            )
            result = runner.run("intent-1", {"path": tmpdir})

        # 200 lines > 100 threshold => WARNING
        assert any(f.severity == Severity.WARNING for f in result.findings)
        assert result.passed is True

    def test_only_scans_python_files(self):
        """Should only scan .py files in a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a large non-Python file (should be ignored)
            txt = os.path.join(tmpdir, "data.txt")
            with open(txt, "w") as f:
                for i in range(6000):
                    f.write(f"line {i}\n")

            # Create a small Python file
            py = os.path.join(tmpdir, "small.py")
            with open(py, "w") as f:
                f.write("x = 1\n")

            runner = RealResourceBoundsRunner()
            result = runner.run("intent-1", {"path": tmpdir})

        assert result.passed is True
        assert result.findings == []

    def test_empty_directory(self):
        """Empty directory should pass with no findings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = RealResourceBoundsRunner()
            result = runner.run("intent-1", {"path": tmpdir})

        assert result.passed is True
        assert result.findings == []


# ---------------------------------------------------------------------------
# Integration: runners work with ValidationGate
# ---------------------------------------------------------------------------


class TestRealRunnersWithGate:
    """Verify real runners plug into ValidationGate correctly."""

    def test_gate_accepts_real_runners(self):
        """Real runners should be accepted by ValidationGate."""
        from src.validation.gate import ValidationGate

        runners = [
            RealStaticAnalysisRunner(),
            RealResourceBoundsRunner(),
        ]

        gate = ValidationGate(runners=runners)

        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = os.path.join(tmpdir, "ok.py")
            with open(py_file, "w") as f:
                f.write("x = 1\n")

            verdict = gate.validate("test-intent", {"path": tmpdir})

        assert verdict.intent_id == "test-intent"
        assert len(verdict.signals) == 2
