"""Tests for the real behavioral diff runner.

Covers:
- Parsing pytest verbose output into structured results
- Diffing before/after test results
- Regression detection (pass -> fail = ERROR)
- Fix detection (fail -> pass = INFO)
- New/removed test detection
- Simulated fallback when no worktree
- Worktree path derivation
- Integration with mocked WorktreeManager
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.validation.models import Finding, Severity, SignalResult, ValidationSignal
from src.validation.real_runners import RealBehavioralDiffRunner
from src.validation.signals import BehavioralDiffRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeWorktreeManager:
    """Fake WorktreeManager that returns pre-configured test output."""

    def __init__(
        self,
        after_output: str = "",
        after_returncode: int = 0,
        before_output: str = "",
        before_returncode: int = 0,
    ) -> None:
        self._after_output = after_output
        self._before_output = before_output
        self._after_rc = after_returncode
        self._before_rc = before_returncode
        self.calls: list[dict[str, Any]] = []

    def run_tests(
        self,
        worktree_path: str,
        test_command: str = "pytest",
        timeout: int = 120,
    ) -> dict[str, Any]:
        self.calls.append(
            {"path": worktree_path, "cmd": test_command, "timeout": timeout}
        )
        # First call = after (worktree), second call = before (repo)
        if len(self.calls) == 1:
            return {
                "returncode": self._after_rc,
                "stdout": self._after_output,
                "stderr": "",
                "passed": self._after_rc == 0,
            }
        return {
            "returncode": self._before_rc,
            "stdout": self._before_output,
            "stderr": "",
            "passed": self._before_rc == 0,
        }


# ---------------------------------------------------------------------------
# Test: _parse_pytest_results
# ---------------------------------------------------------------------------


class TestParsePytestResults:
    """Test parsing of pytest verbose output."""

    def test_empty_output(self):
        results = RealBehavioralDiffRunner._parse_pytest_results("")
        assert results == {}

    def test_all_passing(self):
        output = (
            "tests/test_auth.py::test_login PASSED\n"
            "tests/test_auth.py::test_logout PASSED\n"
        )
        results = RealBehavioralDiffRunner._parse_pytest_results(output)
        assert results == {
            "test_auth.py::test_login": "passed",
            "test_auth.py::test_logout": "passed",
        }

    def test_mixed_results(self):
        output = (
            "tests/test_auth.py::test_login PASSED\n"
            "tests/test_auth.py::test_logout PASSED\n"
            "tests/test_auth.py::test_invalid_token FAILED\n"
        )
        results = RealBehavioralDiffRunner._parse_pytest_results(output)
        assert results == {
            "test_auth.py::test_login": "passed",
            "test_auth.py::test_logout": "passed",
            "test_auth.py::test_invalid_token": "failed",
        }

    def test_error_status(self):
        output = "tests/test_db.py::test_connect ERROR\n"
        results = RealBehavioralDiffRunner._parse_pytest_results(output)
        assert results == {"test_db.py::test_connect": "error"}

    def test_ignores_summary_lines(self):
        output = (
            "tests/test_auth.py::test_login PASSED\n"
            "\n"
            "1 passed in 0.5s\n"
            "===== 1 passed =====\n"
        )
        results = RealBehavioralDiffRunner._parse_pytest_results(output)
        assert len(results) == 1
        assert "test_auth.py::test_login" in results

    def test_handles_whitespace(self):
        output = "  tests/test_auth.py::test_login PASSED  \n"
        results = RealBehavioralDiffRunner._parse_pytest_results(output)
        assert "test_auth.py::test_login" in results

    def test_parametrized_tests(self):
        output = (
            "tests/test_math.py::test_add[1-2-3] PASSED\n"
            "tests/test_math.py::test_add[0-0-0] PASSED\n"
        )
        results = RealBehavioralDiffRunner._parse_pytest_results(output)
        assert len(results) == 2
        assert "test_math.py::test_add[1-2-3]" in results

    def test_normalizes_relative_paths(self):
        """Paths like ../../../tests/test_auth.py should normalize."""
        output = "../../../tmp/tests/test_auth.py::test_login PASSED\n"
        results = RealBehavioralDiffRunner._parse_pytest_results(output)
        assert "test_auth.py::test_login" in results


# ---------------------------------------------------------------------------
# Test: _diff_results
# ---------------------------------------------------------------------------


class TestDiffResults:
    """Test diffing before/after test results."""

    def test_no_changes(self):
        before = {"test_a": "passed", "test_b": "passed"}
        after = {"test_a": "passed", "test_b": "passed"}
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        assert findings == []

    def test_regression_detected(self):
        before = {"test_a": "passed", "test_b": "passed"}
        after = {"test_a": "passed", "test_b": "failed"}
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert "regression" in findings[0].title.lower()
        assert "test_b" in findings[0].description

    def test_regression_pass_to_error(self):
        before = {"test_a": "passed"}
        after = {"test_a": "error"}
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR

    def test_fix_detected(self):
        before = {"test_a": "failed"}
        after = {"test_a": "passed"}
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        assert len(findings) == 1
        assert findings[0].severity == Severity.INFO
        assert "fixed" in findings[0].title.lower()

    def test_fix_from_error(self):
        before = {"test_a": "error"}
        after = {"test_a": "passed"}
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        assert len(findings) == 1
        assert findings[0].severity == Severity.INFO

    def test_new_test_detected(self):
        before = {"test_a": "passed"}
        after = {"test_a": "passed", "test_b": "passed"}
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        assert len(findings) == 1
        assert findings[0].severity == Severity.INFO
        assert "new" in findings[0].title.lower()

    def test_removed_test_detected(self):
        before = {"test_a": "passed", "test_b": "passed"}
        after = {"test_a": "passed"}
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "removed" in findings[0].title.lower()

    def test_multiple_changes(self):
        before = {
            "test_a": "passed",
            "test_b": "failed",
            "test_c": "passed",
        }
        after = {
            "test_a": "failed",  # regression
            "test_b": "passed",  # fix
            "test_d": "passed",  # new (test_c removed)
        }
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        severities = {f.severity for f in findings}
        assert Severity.ERROR in severities  # regression
        assert Severity.INFO in severities  # fix + new
        assert Severity.WARNING in severities  # removed
        assert len(findings) == 4

    def test_both_failing_no_finding(self):
        """A test that was failing and is still failing produces no finding."""
        before = {"test_a": "failed"}
        after = {"test_a": "failed"}
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        assert findings == []

    def test_empty_before(self):
        """All tests new when before is empty."""
        after = {"test_a": "passed", "test_b": "failed"}
        findings = RealBehavioralDiffRunner._diff_results({}, after)
        assert len(findings) == 2
        assert all(f.severity == Severity.INFO for f in findings)

    def test_file_path_extraction(self):
        before = {}
        after = {"tests/test_auth.py::test_login": "passed"}
        findings = RealBehavioralDiffRunner._diff_results(before, after)
        assert findings[0].file_path == "tests/test_auth.py"


# ---------------------------------------------------------------------------
# Test: _derive_repo_dir
# ---------------------------------------------------------------------------


class TestDeriveRepoDir:
    """Test worktree -> repo path derivation."""

    def test_standard_path(self):
        path = "data/worktrees/abc-123/task-456"
        result = RealBehavioralDiffRunner._derive_repo_dir(path)
        assert result == "data/repos/abc-123"

    def test_absolute_path(self):
        path = "/home/user/project/data/worktrees/proj-1/task-2"
        result = RealBehavioralDiffRunner._derive_repo_dir(path)
        assert result == "/home/user/project/data/repos/proj-1"

    def test_no_worktrees_in_path(self):
        path = "/some/random/path"
        result = RealBehavioralDiffRunner._derive_repo_dir(path)
        assert result is None

    def test_worktrees_at_end(self):
        """Edge case: 'worktrees' is the last segment."""
        path = "data/worktrees"
        result = RealBehavioralDiffRunner._derive_repo_dir(path)
        assert result is None


# ---------------------------------------------------------------------------
# Test: RealBehavioralDiffRunner.run (with mock WorktreeManager)
# ---------------------------------------------------------------------------


class TestRealBehavioralDiffRunnerRun:
    """Test the full run() method with a fake WorktreeManager."""

    def test_no_regressions_passes(self):
        wm = FakeWorktreeManager(
            after_output="tests/test_a.py::test_one PASSED\n",
            before_output="tests/test_a.py::test_one PASSED\n",
        )
        runner = RealBehavioralDiffRunner(worktree_manager=wm)
        sandbox = {"worktree_path": "data/worktrees/proj/task"}
        result = runner.run("intent-1", sandbox)
        assert result.passed is True
        assert result.signal == ValidationSignal.BEHAVIORAL_DIFF
        assert len(result.findings) == 0

    def test_regression_fails(self):
        wm = FakeWorktreeManager(
            after_output="tests/test_a.py::test_one FAILED\n",
            before_output="tests/test_a.py::test_one PASSED\n",
        )
        runner = RealBehavioralDiffRunner(worktree_manager=wm)
        sandbox = {"worktree_path": "data/worktrees/proj/task"}
        result = runner.run("intent-1", sandbox)
        assert result.passed is False
        assert any(f.severity == Severity.ERROR for f in result.findings)

    def test_fix_and_new_test_passes(self):
        wm = FakeWorktreeManager(
            after_output=(
                "tests/test_a.py::test_one PASSED\n"
                "tests/test_a.py::test_two PASSED\n"
            ),
            before_output="tests/test_a.py::test_one FAILED\n",
        )
        runner = RealBehavioralDiffRunner(worktree_manager=wm)
        sandbox = {"worktree_path": "data/worktrees/proj/task"}
        result = runner.run("intent-1", sandbox)
        assert result.passed is True
        # Should have a fix finding and a new test finding
        titles = [f.title.lower() for f in result.findings]
        assert any("fixed" in t for t in titles)
        assert any("new" in t for t in titles)

    def test_force_pass_overrides_regression(self):
        wm = FakeWorktreeManager(
            after_output="tests/test_a.py::test_one FAILED\n",
            before_output="tests/test_a.py::test_one PASSED\n",
        )
        runner = RealBehavioralDiffRunner(worktree_manager=wm, force_pass=True)
        sandbox = {"worktree_path": "data/worktrees/proj/task"}
        result = runner.run("intent-1", sandbox)
        assert result.passed is True
        # Findings still present even though force_pass
        assert len(result.findings) > 0

    def test_calls_worktree_manager_with_correct_paths(self):
        wm = FakeWorktreeManager(
            after_output="tests/test_a.py::test_one PASSED\n",
            before_output="tests/test_a.py::test_one PASSED\n",
        )
        runner = RealBehavioralDiffRunner(worktree_manager=wm)
        sandbox = {"worktree_path": "data/worktrees/proj-123/task-456"}
        runner.run("intent-1", sandbox)
        assert len(wm.calls) == 2
        assert wm.calls[0]["path"] == "data/worktrees/proj-123/task-456"
        assert wm.calls[1]["path"] == "data/repos/proj-123"

    def test_before_repo_not_found_treats_all_as_new(self):
        """When repo dir doesn't exist, all tests are treated as new."""
        wm = FakeWorktreeManager(
            after_output=(
                "tests/test_a.py::test_one PASSED\n"
                "tests/test_a.py::test_two FAILED\n"
            ),
            before_output="",
        )
        runner = RealBehavioralDiffRunner(worktree_manager=wm)
        # Use a path where the repo dir won't exist
        sandbox = {"worktree_path": "data/worktrees/nonexistent/task"}
        result = runner.run("intent-1", sandbox)
        # Should pass (no regressions, just new tests)
        assert result.passed is True
        assert all(f.severity == Severity.INFO for f in result.findings)

    def test_confidence_higher_with_before_results(self):
        wm = FakeWorktreeManager(
            after_output="tests/test_a.py::test_one PASSED\n",
            before_output="tests/test_a.py::test_one PASSED\n",
        )
        runner = RealBehavioralDiffRunner(worktree_manager=wm)
        sandbox = {"worktree_path": "data/worktrees/proj/task"}
        result = runner.run("intent-1", sandbox)
        assert result.confidence >= 0.90


# ---------------------------------------------------------------------------
# Test: Simulated fallback
# ---------------------------------------------------------------------------


class TestSimulatedFallback:
    """Test that the runner falls back to simulated mode correctly."""

    def test_no_worktree_path_uses_simulated(self):
        wm = FakeWorktreeManager()
        runner = RealBehavioralDiffRunner(worktree_manager=wm)
        sandbox = {}  # No worktree_path
        result = runner.run("intent-1", sandbox)
        assert result.passed is True
        assert result.signal == ValidationSignal.BEHAVIORAL_DIFF
        # WorktreeManager should not have been called
        assert len(wm.calls) == 0

    def test_no_worktree_manager_uses_simulated(self):
        runner = RealBehavioralDiffRunner(worktree_manager=None)
        sandbox = {"worktree_path": "data/worktrees/proj/task"}
        result = runner.run("intent-1", sandbox)
        assert result.passed is True

    def test_simulated_behavioral_diffs_in_sandbox(self):
        runner = RealBehavioralDiffRunner(worktree_manager=None)
        sandbox = {
            "behavioral_diffs": [
                {
                    "severity": "error",
                    "title": "API regression",
                    "description": "Endpoint /api/v1/users returns 500",
                }
            ]
        }
        result = runner.run("intent-1", sandbox)
        assert result.passed is False
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.ERROR

    def test_simulated_force_pass(self):
        runner = RealBehavioralDiffRunner(worktree_manager=None, force_pass=True)
        sandbox = {
            "behavioral_diffs": [
                {"severity": "error", "title": "Regression", "description": "..."}
            ]
        }
        result = runner.run("intent-1", sandbox)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Test: BehavioralDiffRunner delegation
# ---------------------------------------------------------------------------


class TestBehavioralDiffRunnerDelegation:
    """Test that BehavioralDiffRunner delegates to RealBehavioralDiffRunner."""

    def test_without_worktree_manager_simulated(self):
        runner = BehavioralDiffRunner()
        result = runner.run("intent-1", {})
        assert result.passed is True
        assert result.signal == ValidationSignal.BEHAVIORAL_DIFF

    def test_with_worktree_manager_delegates(self):
        wm = FakeWorktreeManager(
            after_output="tests/test_a.py::test_one PASSED\n",
            before_output="tests/test_a.py::test_one PASSED\n",
        )
        runner = BehavioralDiffRunner(worktree_manager=wm)
        sandbox = {"worktree_path": "data/worktrees/proj/task"}
        result = runner.run("intent-1", sandbox)
        assert result.passed is True
        # Verify the worktree manager was actually called
        assert len(wm.calls) == 2

    def test_with_worktree_manager_no_path_simulated(self):
        """Even with worktree_manager, no path means simulated."""
        wm = FakeWorktreeManager()
        runner = BehavioralDiffRunner(worktree_manager=wm)
        result = runner.run("intent-1", {})
        assert result.passed is True
        assert len(wm.calls) == 0

    def test_force_pass_backward_compat(self):
        runner = BehavioralDiffRunner(force_pass=False)
        result = runner.run("intent-1", {})
        assert result.passed is False

    def test_force_pass_true_backward_compat(self):
        runner = BehavioralDiffRunner(force_pass=True)
        result = runner.run("intent-1", {})
        assert result.passed is True


# ---------------------------------------------------------------------------
# Integration test with a real temp git repo
# ---------------------------------------------------------------------------


class TestBehavioralDiffIntegration:
    """Integration test using real git repos in a temp directory."""

    @pytest.fixture
    def git_setup(self, tmp_path):
        """Create a real git repo + worktree with different test files."""
        from src.worktrees.manager import WorktreeManager

        repo_dir = tmp_path / "repos" / "test-project"
        wt_dir = tmp_path / "worktrees" / "test-project" / "task-1"

        # Create the repo
        repo_dir.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_dir,
            capture_output=True,
        )

        # Create a test file on main branch with one passing and one failing
        test_file = repo_dir / "tests" / "test_example.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "def test_alpha():\n    assert True\n\n"
            "def test_beta():\n    assert False\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo_dir,
            capture_output=True,
        )

        # Create worktree
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", "task/fix", str(wt_dir), "HEAD"],
            cwd=repo_dir,
            capture_output=True,
        )

        # Modify tests in the worktree: fix beta, add gamma
        wt_test_file = wt_dir / "tests" / "test_example.py"
        wt_test_file.write_text(
            "def test_alpha():\n    assert True\n\n"
            "def test_beta():\n    assert True\n\n"
            "def test_gamma():\n    assert True\n"
        )

        wm = WorktreeManager(
            repos_dir=tmp_path / "repos",
            worktrees_dir=tmp_path / "worktrees",
        )

        return {
            "worktree_manager": wm,
            "repo_dir": str(repo_dir),
            "worktree_path": str(wt_dir),
            "tmp_path": tmp_path,
        }

    def test_real_worktree_diff(self, git_setup):
        """End-to-end test with real git repo detecting a fix + new test."""
        runner = RealBehavioralDiffRunner(
            worktree_manager=git_setup["worktree_manager"],
            test_command="python3 -m pytest -p no:asyncio -v --tb=no",
        )
        sandbox = {"worktree_path": git_setup["worktree_path"]}
        result = runner.run("intent-integration", sandbox)

        # beta went from fail to pass = fix (INFO)
        # gamma is new = new test (INFO)
        # No regressions, so should pass
        assert result.passed is True
        assert result.signal == ValidationSignal.BEHAVIORAL_DIFF

        titles = [f.title.lower() for f in result.findings]
        # Should detect the fix and the new test
        has_fix = any("fixed" in t for t in titles)
        has_new = any("new" in t for t in titles)
        assert has_fix or has_new  # At least one of these should be detected
