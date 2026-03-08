"""Checks changed files against a constraint set and reports violations."""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from .models import (
    CheckType,
    Constraint,
    ConstraintCheckResult,
    ConstraintSet,
    ConstraintSeverity,
    ConstraintViolation,
)


class ConstraintChecker:
    """Evaluates changed files against a :class:`ConstraintSet`."""

    def check(
        self,
        constraint_set: ConstraintSet,
        changed_files: dict[str, str],
    ) -> ConstraintCheckResult:
        """Check all constraints against the changed files.

        Args:
            constraint_set: The set of constraints to enforce.
            changed_files: Mapping of ``file_path -> file_content`` for every
                file that was changed.

        Returns:
            A :class:`ConstraintCheckResult` with categorised violations.
        """
        all_violations: list[ConstraintViolation] = []

        for constraint in constraint_set.constraints:
            for file_path, content in changed_files.items():
                if not self._applies(constraint, file_path):
                    continue
                violations = self.check_file(constraint, file_path, content)
                all_violations.extend(violations)

        must_violations = [
            v for v in all_violations
            if v.constraint.severity == ConstraintSeverity.MUST
        ]
        should_violations = [
            v for v in all_violations
            if v.constraint.severity == ConstraintSeverity.SHOULD
        ]
        prefer_violations = [
            v for v in all_violations
            if v.constraint.severity == ConstraintSeverity.PREFER
        ]

        return ConstraintCheckResult(
            violations=all_violations,
            passed=len(must_violations) == 0,
            warnings=should_violations,
            suggestions=prefer_violations,
        )

    def check_file(
        self,
        constraint: Constraint,
        file_path: str,
        content: str,
    ) -> list[ConstraintViolation]:
        """Check a single constraint against one file.

        Args:
            constraint: The constraint to check.
            file_path: Path of the file being checked.
            content: Full text content of the file.

        Returns:
            A list of :class:`ConstraintViolation` instances (may be empty).
        """
        check_type = constraint.enforcement.check_type

        if check_type == CheckType.FILE_PATTERN:
            return self._check_file_pattern(constraint, file_path, content)
        elif check_type == CheckType.DEPENDENCY_CHECK:
            return self._check_dependency(constraint, file_path, content)
        elif check_type == CheckType.CUSTOM:
            # Custom checks are named but not executed inline — return empty.
            # A real system would dispatch to a registry of custom checkers.
            return []

        return []

    def format_for_agent(self, result: ConstraintCheckResult) -> dict[str, Any]:
        """Produce a machine-readable dict that agents can act on.

        Args:
            result: The constraint check result to format.

        Returns:
            A dictionary with structured information about violations.
        """
        def _violation_dict(v: ConstraintViolation) -> dict[str, Any]:
            return {
                "constraint_id": v.constraint.constraint_id,
                "category": v.constraint.category.value,
                "severity": v.constraint.severity.value,
                "rule": v.constraint.rule,
                "file_path": v.file_path,
                "line_number": v.line_number,
                "description": v.description,
                "suggestion": v.suggestion,
            }

        return {
            "passed": result.passed,
            "total_violations": len(result.violations),
            "must_violations": [_violation_dict(v) for v in result.violations
                                if v.constraint.severity == ConstraintSeverity.MUST],
            "should_warnings": [_violation_dict(v) for v in result.warnings],
            "prefer_suggestions": [_violation_dict(v) for v in result.suggestions],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _applies(constraint: Constraint, file_path: str) -> bool:
        """Return True if the constraint applies to the given file path."""
        applies_to = constraint.applies_to

        # Check path globs — empty means "all paths"
        if applies_to.paths:
            if not any(fnmatch.fnmatch(file_path, p) for p in applies_to.paths):
                return False

        # Check file type globs — empty means "all types"
        if applies_to.file_types:
            if not any(fnmatch.fnmatch(file_path, ft) for ft in applies_to.file_types):
                return False

        return True

    @staticmethod
    def _check_file_pattern(
        constraint: Constraint,
        file_path: str,
        content: str,
    ) -> list[ConstraintViolation]:
        """Scan content for forbidden patterns (string or regex match)."""
        violations: list[ConstraintViolation] = []
        lines = content.splitlines()

        for forbidden in constraint.enforcement.forbidden_patterns:
            try:
                pattern = re.compile(forbidden)
            except re.error:
                # Fall back to literal substring match
                pattern = None

            for line_num, line in enumerate(lines, start=1):
                matched = False
                if pattern is not None:
                    matched = bool(pattern.search(line))
                else:
                    matched = forbidden in line

                if matched:
                    violations.append(
                        ConstraintViolation(
                            constraint=constraint,
                            file_path=file_path,
                            line_number=line_num,
                            description=(
                                f"Forbidden pattern '{forbidden}' found: "
                                f"{line.strip()}"
                            ),
                            suggestion=(
                                f"Remove or replace the pattern matching "
                                f"'{forbidden}' to comply with rule "
                                f"'{constraint.constraint_id}'"
                            ),
                        )
                    )

        return violations

    @staticmethod
    def _check_dependency(
        constraint: Constraint,
        file_path: str,
        content: str,
    ) -> list[ConstraintViolation]:
        """Check import statements against forbidden patterns."""
        violations: list[ConstraintViolation] = []
        lines = content.splitlines()

        for line_num, line in enumerate(lines, start=1):
            stripped = line.strip()
            # Only inspect import lines
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue

            for forbidden in constraint.enforcement.forbidden_patterns:
                try:
                    pattern = re.compile(forbidden)
                    matched = bool(pattern.search(stripped))
                except re.error:
                    matched = forbidden in stripped

                if matched:
                    violations.append(
                        ConstraintViolation(
                            constraint=constraint,
                            file_path=file_path,
                            line_number=line_num,
                            description=(
                                f"Forbidden dependency '{forbidden}' imported: "
                                f"{stripped}"
                            ),
                            suggestion=(
                                f"Remove the import matching '{forbidden}' "
                                f"and use an approved alternative per rule "
                                f"'{constraint.constraint_id}'"
                            ),
                        )
                    )

        return violations
