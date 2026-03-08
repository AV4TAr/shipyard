"""Pydantic v2 models for the Architectural Constraints System."""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, Field


class ConstraintSeverity(str, enum.Enum):
    """How strictly the constraint is enforced."""

    MUST = "must"        # Hard rule — blocks deploy
    SHOULD = "should"    # Soft rule — warning
    PREFER = "prefer"    # Suggestion — info-level


class ConstraintCategory(str, enum.Enum):
    """Domain category of the constraint."""

    ARCHITECTURE = "architecture"
    SECURITY = "security"
    TESTING = "testing"
    DEPENDENCIES = "dependencies"
    PERFORMANCE = "performance"
    STYLE = "style"
    OPERATIONS = "operations"


class CheckType(str, enum.Enum):
    """How a constraint is checked."""

    FILE_PATTERN = "file_pattern"
    DEPENDENCY_CHECK = "dependency_check"
    CUSTOM = "custom"


class EnforcementConfig(BaseModel):
    """How to check a constraint."""

    check_type: CheckType
    patterns: list[str] = Field(
        default_factory=list,
        description="File patterns, import patterns, etc. to check",
    )
    forbidden_patterns: list[str] = Field(
        default_factory=list,
        description="Patterns that violate the rule",
    )
    custom_check: Optional[str] = Field(
        default=None,
        description="Name of a custom checker function",
    )


class AppliesTo(BaseModel):
    """Scope of a constraint — which files/services it applies to."""

    services: list[str] = Field(
        default_factory=list,
        description="Service names; empty means all services",
    )
    paths: list[str] = Field(
        default_factory=list,
        description="Glob patterns for file paths; empty means all paths",
    )
    file_types: list[str] = Field(
        default_factory=list,
        description="File type globs, e.g. '*.py'; empty means all types",
    )


class Constraint(BaseModel):
    """A single architectural rule."""

    constraint_id: str = Field(
        description="Human-readable identifier, e.g. 'no-raw-sql'",
    )
    category: ConstraintCategory
    severity: ConstraintSeverity
    rule: str = Field(
        description="Human-readable description of the rule",
    )
    rationale: str = Field(
        description="Why this rule exists — helps agents understand intent",
    )
    enforcement: EnforcementConfig
    applies_to: AppliesTo = Field(default_factory=AppliesTo)


class ConstraintSet(BaseModel):
    """A named collection of constraints."""

    name: str
    description: str
    version: str
    constraints: list[Constraint] = Field(default_factory=list)


class ConstraintViolation(BaseModel):
    """A detected constraint violation."""

    constraint: Constraint
    file_path: str
    line_number: Optional[int] = None
    description: str
    suggestion: str = Field(
        description="Machine-readable fix suggestion for agents",
    )


class ConstraintCheckResult(BaseModel):
    """Aggregate result of checking all constraints."""

    violations: list[ConstraintViolation] = Field(default_factory=list)
    passed: bool = Field(
        description="True when there are no MUST violations",
    )
    warnings: list[ConstraintViolation] = Field(
        default_factory=list,
        description="SHOULD-severity violations",
    )
    suggestions: list[ConstraintViolation] = Field(
        default_factory=list,
        description="PREFER-severity violations",
    )
