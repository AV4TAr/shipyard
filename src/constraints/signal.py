"""Integrates constraint checking into the validation pipeline as a signal."""

from __future__ import annotations

import time
from typing import Any, Optional

from ..validation.models import Finding, Severity, SignalResult, ValidationSignal
from ..validation.signals import ValidationSignalRunner
from .checker import ConstraintChecker
from .loader import ConstraintLoader
from .models import ConstraintCheckResult, ConstraintSet, ConstraintSeverity


# Extend ValidationSignal to include the new constraint check signal.
# Since ValidationSignal is an enum and cannot be extended at runtime,
# we reuse STATIC_ANALYSIS as the signal type and distinguish via findings.
# In a production system you would register a new signal type.
_CONSTRAINT_SIGNAL = ValidationSignal.STATIC_ANALYSIS

_SEVERITY_MAP: dict[ConstraintSeverity, Severity] = {
    ConstraintSeverity.MUST: Severity.ERROR,
    ConstraintSeverity.SHOULD: Severity.WARNING,
    ConstraintSeverity.PREFER: Severity.INFO,
}


class ConstraintSignalRunner(ValidationSignalRunner):
    """Runs architectural constraint checks as a validation signal.

    This integrates the :class:`ConstraintChecker` into the existing
    :class:`~src.validation.gate.ValidationGate` pipeline so that
    constraints are enforced alongside other validation signals.

    The runner expects ``sandbox_result`` to contain:
        - ``changed_files``: ``dict[str, str]`` mapping file paths to content.
        - Optionally ``constraints_path``: path to a YAML constraint file.
          If not provided, the runner uses the ``constraint_set`` given at
          construction time.
    """

    def __init__(
        self,
        *,
        constraint_set: Optional[ConstraintSet] = None,
        force_pass: Optional[bool] = None,
    ) -> None:
        self.constraint_set = constraint_set
        self.force_pass = force_pass
        self._checker = ConstraintChecker()
        self._loader = ConstraintLoader()

    def run(
        self, intent_id: str, sandbox_result: dict[str, Any]
    ) -> SignalResult:
        """Run constraint checks and return a :class:`SignalResult`.

        Args:
            intent_id: The identifier of the intent being validated.
            sandbox_result: Must contain ``changed_files`` (dict[str, str]).
                May contain ``constraints_path`` (str) to load constraints
                from YAML at runtime.

        Returns:
            A ``SignalResult`` with findings mapped from constraint violations.
        """
        start = time.monotonic()

        # Resolve the constraint set
        cs = self._resolve_constraint_set(sandbox_result)

        changed_files: dict[str, str] = sandbox_result.get("changed_files", {})
        check_result = self._checker.check(cs, changed_files)

        findings = self._to_findings(check_result)

        if self.force_pass is not None:
            passed = self.force_pass
        else:
            passed = check_result.passed

        confidence = 0.95 if passed else 0.90
        duration = time.monotonic() - start

        return SignalResult(
            signal=_CONSTRAINT_SIGNAL,
            passed=passed,
            confidence=confidence,
            findings=findings,
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_constraint_set(
        self, sandbox_result: dict[str, Any]
    ) -> ConstraintSet:
        """Get the constraint set from config or sandbox_result."""
        constraints_path = sandbox_result.get("constraints_path")
        if constraints_path:
            return self._loader.load_from_yaml(constraints_path)
        if self.constraint_set is not None:
            return self.constraint_set
        # Return an empty constraint set if nothing is configured
        return ConstraintSet(
            name="empty", description="No constraints configured", version="0.0.0"
        )

    @staticmethod
    def _to_findings(check_result: ConstraintCheckResult) -> list[Finding]:
        """Convert constraint violations to validation findings."""
        findings: list[Finding] = []
        for violation in check_result.violations:
            severity = _SEVERITY_MAP.get(
                violation.constraint.severity, Severity.WARNING
            )
            findings.append(
                Finding(
                    severity=severity,
                    title=f"Constraint violation: {violation.constraint.constraint_id}",
                    description=violation.description,
                    file_path=violation.file_path,
                    line_number=violation.line_number,
                    suggestion=violation.suggestion,
                )
            )
        return findings
