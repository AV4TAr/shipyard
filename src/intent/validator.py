"""Intent validation logic: scope checks, risk classification, conflict detection."""

from __future__ import annotations

import fnmatch
from typing import Sequence

from .schema import IntentDeclaration, IntentVerdict, RiskLevel, ScopeConstraint

# Patterns used for automatic risk classification.
_RISK_PATTERNS: list[tuple[str, RiskLevel]] = [
    ("**/migrations/**", RiskLevel.HIGH),
    ("**/schema*", RiskLevel.HIGH),
    ("docker*", RiskLevel.HIGH),
    ("**/docker*", RiskLevel.HIGH),
    ("ci/*", RiskLevel.HIGH),
    (".github/**", RiskLevel.HIGH),
    ("**/Dockerfile*", RiskLevel.HIGH),
    ("**/deploy/**", RiskLevel.CRITICAL),
    ("**/prod/**", RiskLevel.CRITICAL),
    ("**/*.env", RiskLevel.CRITICAL),
    ("**/secrets/**", RiskLevel.CRITICAL),
    ("**/test*/**", RiskLevel.LOW),
    ("**/tests/**", RiskLevel.LOW),
    ("**/*_test.py", RiskLevel.LOW),
    ("**/test_*.py", RiskLevel.LOW),
]


def _match(pattern: str, path: str) -> bool:
    """Check whether *path* matches a glob *pattern*.

    Supports ``**`` via :func:`fnmatch.fnmatch` after normalising separators.
    ``fnmatch`` doesn't natively handle ``**``, so we translate ``**`` into a
    catch-all wildcard before matching.
    """
    # Normalise: translate ** to a single * for fnmatch (which treats * as
    # matching everything including '/').  This is intentionally simple —
    # production systems should use pathlib or gitignore-style matching.
    normalised = pattern.replace("**", "*")
    return fnmatch.fnmatch(path, normalised)


class IntentValidator:
    """Validates an :class:`IntentDeclaration` against scope and risk rules."""

    def validate(
        self,
        intent: IntentDeclaration,
        constraints: Sequence[ScopeConstraint],
        active_intents: Sequence[IntentDeclaration] | None = None,
    ) -> IntentVerdict:
        denial_reasons: list[str] = []
        conditions: list[str] = []

        # --- scope checks ---
        agent_constraints = [c for c in constraints if c.agent_id == intent.agent_id]
        if agent_constraints:
            self._check_scope(intent, agent_constraints, denial_reasons)

        # --- risk classification ---
        risk_level = self._classify_risk(intent)

        # --- max risk enforcement ---
        for sc in agent_constraints:
            if self._risk_exceeds(risk_level, sc.max_risk_level):
                denial_reasons.append(
                    f"Risk level {risk_level.value} exceeds maximum "
                    f"allowed {sc.max_risk_level.value} for agent {intent.agent_id}"
                )

        # --- conflict detection ---
        conflicts = self._detect_conflicts(intent, active_intents or [])

        if conflicts:
            conditions.append(
                "Conflicting intents must be resolved before proceeding"
            )

        approved = len(denial_reasons) == 0
        return IntentVerdict(
            intent_id=intent.intent_id,
            approved=approved,
            risk_level=risk_level,
            denial_reasons=denial_reasons,
            conflicts=conflicts,
            conditions=conditions,
        )

    # ------------------------------------------------------------------
    # Scope
    # ------------------------------------------------------------------

    @staticmethod
    def _check_scope(
        intent: IntentDeclaration,
        constraints: Sequence[ScopeConstraint],
        denial_reasons: list[str],
    ) -> None:
        for constraint in constraints:
            # File path checks
            for target in intent.target_files:
                # Denied paths take precedence
                for denied in constraint.denied_paths:
                    if _match(denied, target):
                        denial_reasons.append(
                            f"File '{target}' matches denied path '{denied}'"
                        )

                # Must match at least one allowed path (if any are specified)
                if constraint.allowed_paths:
                    if not any(
                        _match(allowed, target)
                        for allowed in constraint.allowed_paths
                    ):
                        denial_reasons.append(
                            f"File '{target}' is not within any allowed path"
                        )

            # Service checks
            if constraint.allowed_services:
                for svc in intent.target_services:
                    if svc not in constraint.allowed_services:
                        denial_reasons.append(
                            f"Service '{svc}' is not in the allowed services list"
                        )

    # ------------------------------------------------------------------
    # Risk classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_risk(intent: IntentDeclaration) -> RiskLevel:
        """Return the highest risk level among the intent's target files.

        Each file is classified individually (first matching pattern wins for
        that file, defaulting to MEDIUM).  The overall risk is the maximum
        across all files.
        """
        order = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }
        overall = RiskLevel.LOW  # will be raised by individual file levels

        for target in intent.target_files:
            file_level = RiskLevel.MEDIUM  # default for unmatched files
            for pattern, level in _RISK_PATTERNS:
                if _match(pattern, target):
                    file_level = level
                    break  # first match wins for this file
            if order[file_level] > order[overall]:
                overall = file_level

        return overall

    @staticmethod
    def _risk_exceeds(a: RiskLevel, b: RiskLevel) -> bool:
        """Return True if risk level *a* is strictly higher than *b*."""
        order = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }
        return order[a] > order[b]

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_conflicts(
        intent: IntentDeclaration,
        active_intents: Sequence[IntentDeclaration],
    ) -> list:
        """Find active intents that touch overlapping files."""
        conflicting_ids: list = []
        for active in active_intents:
            if active.intent_id == intent.intent_id:
                continue
            for target in intent.target_files:
                for active_target in active.target_files:
                    if _match(target, active_target) or _match(active_target, target):
                        conflicting_ids.append(active.intent_id)
                        break
                else:
                    continue
                break
        return conflicting_ids
