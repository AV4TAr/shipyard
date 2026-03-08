"""Constraints endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from src.constraints.loader import ConstraintLoader, ConstraintLoadError

router = APIRouter(prefix="/api/constraints", tags=["constraints"])

_CONSTRAINTS_PATH = Path(__file__).resolve().parents[3] / "configs" / "constraints.yaml"


@router.get("")
def list_constraints():
    """List loaded constraints."""
    loader = ConstraintLoader()
    try:
        constraint_set = loader.load_from_yaml(str(_CONSTRAINTS_PATH))
    except ConstraintLoadError:
        return []
    return [c.model_dump(mode="json") for c in constraint_set.constraints]
