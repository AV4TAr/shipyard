"""Architectural Constraints System.

Humans define rules once, agents read them before every task,
the pipeline enforces them automatically.
"""

from .checker import ConstraintChecker
from .loader import ConstraintLoader
from .models import (
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
from .signal import ConstraintSignalRunner

__all__ = [
    "AppliesTo",
    "CheckType",
    "Constraint",
    "ConstraintCategory",
    "ConstraintCheckResult",
    "ConstraintChecker",
    "ConstraintLoader",
    "ConstraintSet",
    "ConstraintSeverity",
    "ConstraintSignalRunner",
    "ConstraintViolation",
    "EnforcementConfig",
]
