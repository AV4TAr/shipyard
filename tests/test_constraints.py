"""Tests for the Architectural Constraints System."""

from __future__ import annotations

import os
import textwrap

import pytest

from src.constraints.checker import ConstraintChecker
from src.constraints.loader import ConstraintLoader, ConstraintLoadError
from src.constraints.models import (
    AppliesTo,
    CheckType,
    Constraint,
    ConstraintCategory,
    ConstraintCheckResult,
    ConstraintSet,
    ConstraintSeverity,
    ConstraintViolation,
    EnforcementConfig,
)
from src.constraints.signal import ConstraintSignalRunner


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

YAML_PATH = os.path.join(
    os.path.dirname(__file__), "..", "configs", "constraints.yaml"
)


def _make_constraint(
    *,
    constraint_id: str = "test-rule",
    category: ConstraintCategory = ConstraintCategory.SECURITY,
    severity: ConstraintSeverity = ConstraintSeverity.MUST,
    rule: str = "Test rule",
    rationale: str = "For testing",
    check_type: CheckType = CheckType.FILE_PATTERN,
    forbidden_patterns: list[str] | None = None,
    paths: list[str] | None = None,
    file_types: list[str] | None = None,
) -> Constraint:
    return Constraint(
        constraint_id=constraint_id,
        category=category,
        severity=severity,
        rule=rule,
        rationale=rationale,
        enforcement=EnforcementConfig(
            check_type=check_type,
            forbidden_patterns=forbidden_patterns or [],
        ),
        applies_to=AppliesTo(
            paths=paths or [],
            file_types=file_types or [],
        ),
    )


def _make_constraint_set(*constraints: Constraint) -> ConstraintSet:
    return ConstraintSet(
        name="test-set",
        description="Test constraint set",
        version="1.0.0",
        constraints=list(constraints),
    )


# ------------------------------------------------------------------
# Loader tests
# ------------------------------------------------------------------

class TestConstraintLoader:
    def test_load_from_yaml(self) -> None:
        loader = ConstraintLoader()
        cs = loader.load_from_yaml(YAML_PATH)

        assert cs.name == "core-platform-constraints"
        assert cs.version == "1.0.0"
        assert len(cs.constraints) > 0

    def test_load_from_yaml_missing_file(self) -> None:
        loader = ConstraintLoader()
        with pytest.raises(ConstraintLoadError, match="not found"):
            loader.load_from_yaml("/nonexistent/path.yaml")

    def test_load_from_dict(self) -> None:
        loader = ConstraintLoader()
        data = {
            "name": "test",
            "description": "Test set",
            "version": "0.1.0",
            "constraints": [
                {
                    "constraint_id": "no-eval",
                    "category": "security",
                    "severity": "must",
                    "rule": "Do not use eval",
                    "rationale": "Security risk",
                    "enforcement": {
                        "check_type": "file_pattern",
                        "forbidden_patterns": ["\\beval\\s*\\("],
                    },
                }
            ],
        }
        cs = loader.load_from_dict(data)

        assert cs.name == "test"
        assert len(cs.constraints) == 1
        assert cs.constraints[0].constraint_id == "no-eval"
        assert cs.constraints[0].severity == ConstraintSeverity.MUST

    def test_load_from_dict_invalid(self) -> None:
        loader = ConstraintLoader()
        with pytest.raises(ConstraintLoadError, match="Invalid constraint data"):
            loader.load_from_dict({"name": "bad"})  # missing required fields


# ------------------------------------------------------------------
# FILE_PATTERN checking
# ------------------------------------------------------------------

class TestFilePatternCheck:
    def test_forbidden_pattern_found(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["\\beval\\s*\\("],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        result = checker.check(cs, {"app.py": "result = eval('1+1')\n"})

        assert not result.passed
        assert len(result.violations) == 1
        assert result.violations[0].file_path == "app.py"
        assert result.violations[0].line_number == 1

    def test_no_violation_when_clean(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["\\beval\\s*\\("],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        result = checker.check(cs, {"app.py": "result = safe_parse('1+1')\n"})

        assert result.passed
        assert len(result.violations) == 0

    def test_multiple_violations_same_file(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["\\beval\\s*\\(", "\\bexec\\s*\\("],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        code = textwrap.dedent("""\
            x = eval("1+1")
            y = exec("print('hi')")
        """)
        result = checker.check(cs, {"app.py": code})

        assert not result.passed
        assert len(result.violations) == 2


# ------------------------------------------------------------------
# DEPENDENCY_CHECK
# ------------------------------------------------------------------

class TestDependencyCheck:
    def test_forbidden_import_found(self) -> None:
        constraint = _make_constraint(
            check_type=CheckType.DEPENDENCY_CHECK,
            forbidden_patterns=["\\bflask\\b"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        code = "from flask import Flask\n\napp = Flask(__name__)\n"
        result = checker.check(cs, {"app.py": code})

        assert not result.passed
        assert len(result.violations) == 1
        assert result.violations[0].line_number == 1

    def test_no_violation_for_allowed_import(self) -> None:
        constraint = _make_constraint(
            check_type=CheckType.DEPENDENCY_CHECK,
            forbidden_patterns=["\\bflask\\b"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        code = "from fastapi import FastAPI\n\napp = FastAPI()\n"
        result = checker.check(cs, {"app.py": code})

        assert result.passed
        assert len(result.violations) == 0

    def test_dependency_check_only_scans_imports(self) -> None:
        """Non-import lines containing the forbidden pattern are ignored."""
        constraint = _make_constraint(
            check_type=CheckType.DEPENDENCY_CHECK,
            forbidden_patterns=["\\bflask\\b"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        code = "# We used to use flask but migrated to fastapi\nx = 1\n"
        result = checker.check(cs, {"app.py": code})

        assert result.passed


# ------------------------------------------------------------------
# Severity categorisation
# ------------------------------------------------------------------

class TestSeverityCategorisation:
    def test_must_blocks(self) -> None:
        must = _make_constraint(
            constraint_id="must-rule",
            severity=ConstraintSeverity.MUST,
            forbidden_patterns=["bad_pattern"],
        )
        cs = _make_constraint_set(must)
        checker = ConstraintChecker()

        result = checker.check(cs, {"f.py": "bad_pattern\n"})
        assert not result.passed

    def test_should_warns(self) -> None:
        should = _make_constraint(
            constraint_id="should-rule",
            severity=ConstraintSeverity.SHOULD,
            forbidden_patterns=["warn_pattern"],
        )
        cs = _make_constraint_set(should)
        checker = ConstraintChecker()

        result = checker.check(cs, {"f.py": "warn_pattern\n"})
        assert result.passed  # SHOULD does not block
        assert len(result.warnings) == 1

    def test_prefer_suggests(self) -> None:
        prefer = _make_constraint(
            constraint_id="prefer-rule",
            severity=ConstraintSeverity.PREFER,
            forbidden_patterns=["suggest_pattern"],
        )
        cs = _make_constraint_set(prefer)
        checker = ConstraintChecker()

        result = checker.check(cs, {"f.py": "suggest_pattern\n"})
        assert result.passed  # PREFER does not block
        assert len(result.suggestions) == 1

    def test_mixed_severities(self) -> None:
        must = _make_constraint(
            constraint_id="must-rule",
            severity=ConstraintSeverity.MUST,
            forbidden_patterns=["must_bad"],
        )
        should = _make_constraint(
            constraint_id="should-rule",
            severity=ConstraintSeverity.SHOULD,
            forbidden_patterns=["should_bad"],
        )
        prefer = _make_constraint(
            constraint_id="prefer-rule",
            severity=ConstraintSeverity.PREFER,
            forbidden_patterns=["prefer_bad"],
        )
        cs = _make_constraint_set(must, should, prefer)
        checker = ConstraintChecker()

        code = "must_bad\nshould_bad\nprefer_bad\n"
        result = checker.check(cs, {"f.py": code})

        assert not result.passed
        assert len(result.violations) == 3
        assert len(result.warnings) == 1
        assert len(result.suggestions) == 1


# ------------------------------------------------------------------
# AppliesTo scoping
# ------------------------------------------------------------------

class TestAppliesToScoping:
    def test_constraint_applies_to_matching_path(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["bad"],
            paths=["src/api/*"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        result = checker.check(cs, {"src/api/routes.py": "bad\n"})
        assert not result.passed
        assert len(result.violations) == 1

    def test_constraint_skips_non_matching_path(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["bad"],
            paths=["src/api/*"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        result = checker.check(cs, {"src/utils/helper.py": "bad\n"})
        assert result.passed
        assert len(result.violations) == 0

    def test_constraint_applies_to_matching_file_type(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["bad"],
            file_types=["*.py"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        result = checker.check(cs, {"app.py": "bad\n"})
        assert not result.passed

    def test_constraint_skips_non_matching_file_type(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["bad"],
            file_types=["*.py"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        result = checker.check(cs, {"config.yaml": "bad\n"})
        assert result.passed

    def test_empty_applies_to_matches_everything(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["bad"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        result = checker.check(cs, {"anything/at/all.txt": "bad\n"})
        assert not result.passed


# ------------------------------------------------------------------
# ConstraintSignalRunner integration
# ------------------------------------------------------------------

class TestConstraintSignalRunner:
    def test_run_returns_signal_result(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["\\beval\\s*\\("],
        )
        cs = _make_constraint_set(constraint)
        runner = ConstraintSignalRunner(constraint_set=cs)

        result = runner.run("intent-1", {
            "changed_files": {"app.py": "x = eval('1+1')\n"},
        })

        assert not result.passed
        assert len(result.findings) == 1
        assert result.findings[0].file_path == "app.py"
        assert result.duration_seconds >= 0

    def test_run_passes_with_clean_code(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["\\beval\\s*\\("],
        )
        cs = _make_constraint_set(constraint)
        runner = ConstraintSignalRunner(constraint_set=cs)

        result = runner.run("intent-2", {
            "changed_files": {"app.py": "x = safe_parse('1+1')\n"},
        })

        assert result.passed
        assert len(result.findings) == 0

    def test_run_with_no_constraint_set(self) -> None:
        """No constraints configured — should pass with no findings."""
        runner = ConstraintSignalRunner()

        result = runner.run("intent-3", {"changed_files": {"f.py": "eval('x')"}})

        assert result.passed
        assert len(result.findings) == 0

    def test_run_loads_from_yaml_path(self) -> None:
        runner = ConstraintSignalRunner()

        # Use the real YAML config — with code that violates no-eval-exec
        result = runner.run("intent-4", {
            "changed_files": {"app.py": "x = eval('danger')\n"},
            "constraints_path": YAML_PATH,
        })

        assert not result.passed
        assert any(
            "no-eval-exec" in f.title for f in result.findings
        )

    def test_force_pass_overrides(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["bad"],
        )
        cs = _make_constraint_set(constraint)
        runner = ConstraintSignalRunner(constraint_set=cs, force_pass=True)

        result = runner.run("intent-5", {
            "changed_files": {"f.py": "bad\n"},
        })

        assert result.passed


# ------------------------------------------------------------------
# format_for_agent
# ------------------------------------------------------------------

class TestFormatForAgent:
    def test_output_structure(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["bad"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        result = checker.check(cs, {"f.py": "bad\n"})
        output = checker.format_for_agent(result)

        assert isinstance(output, dict)
        assert "passed" in output
        assert "total_violations" in output
        assert "must_violations" in output
        assert "should_warnings" in output
        assert "prefer_suggestions" in output

        assert output["passed"] is False
        assert output["total_violations"] == 1
        assert len(output["must_violations"]) == 1

        violation = output["must_violations"][0]
        assert "constraint_id" in violation
        assert "category" in violation
        assert "severity" in violation
        assert "rule" in violation
        assert "file_path" in violation
        assert "line_number" in violation
        assert "description" in violation
        assert "suggestion" in violation

    def test_clean_output(self) -> None:
        constraint = _make_constraint(
            forbidden_patterns=["bad"],
        )
        cs = _make_constraint_set(constraint)
        checker = ConstraintChecker()

        result = checker.check(cs, {"f.py": "good\n"})
        output = checker.format_for_agent(result)

        assert output["passed"] is True
        assert output["total_violations"] == 0
        assert output["must_violations"] == []
        assert output["should_warnings"] == []
        assert output["prefer_suggestions"] == []


# ------------------------------------------------------------------
# Passed = true when no MUST violations
# ------------------------------------------------------------------

class TestPassedLogic:
    def test_passed_true_with_no_violations(self) -> None:
        cs = _make_constraint_set()
        checker = ConstraintChecker()
        result = checker.check(cs, {"f.py": "clean code\n"})
        assert result.passed

    def test_passed_true_with_only_should_and_prefer(self) -> None:
        should = _make_constraint(
            constraint_id="s",
            severity=ConstraintSeverity.SHOULD,
            forbidden_patterns=["warn"],
        )
        prefer = _make_constraint(
            constraint_id="p",
            severity=ConstraintSeverity.PREFER,
            forbidden_patterns=["hint"],
        )
        cs = _make_constraint_set(should, prefer)
        checker = ConstraintChecker()

        result = checker.check(cs, {"f.py": "warn\nhint\n"})

        assert result.passed
        assert len(result.warnings) == 1
        assert len(result.suggestions) == 1

    def test_passed_false_with_must_violation(self) -> None:
        must = _make_constraint(
            severity=ConstraintSeverity.MUST,
            forbidden_patterns=["danger"],
        )
        cs = _make_constraint_set(must)
        checker = ConstraintChecker()

        result = checker.check(cs, {"f.py": "danger\n"})
        assert not result.passed
