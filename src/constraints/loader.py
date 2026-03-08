"""Loads constraint sets from YAML files or dicts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Requires PyYAML — add `pyyaml` to project dependencies.
import yaml
from pydantic import ValidationError

from .models import ConstraintSet


class ConstraintLoadError(Exception):
    """Raised when a constraint file or dict is malformed."""


class ConstraintLoader:
    """Loads and validates :class:`ConstraintSet` instances."""

    def load_from_yaml(self, path: str) -> ConstraintSet:
        """Load a constraint set from a YAML file.

        Args:
            path: Filesystem path to the YAML file.

        Returns:
            A validated ``ConstraintSet``.

        Raises:
            ConstraintLoadError: If the file cannot be read or parsed, or
                if the structure does not match the expected schema.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise ConstraintLoadError(f"Constraint file not found: {path}")

        try:
            raw = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConstraintLoadError(
                f"Failed to read constraint file {path}: {exc}"
            ) from exc

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ConstraintLoadError(
                f"Invalid YAML in {path}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise ConstraintLoadError(
                f"Expected a YAML mapping at top level in {path}, "
                f"got {type(data).__name__}"
            )

        return self.load_from_dict(data)

    def load_from_dict(self, data: dict[str, Any]) -> ConstraintSet:
        """Load a constraint set from a dictionary.

        Args:
            data: Dictionary matching the ``ConstraintSet`` schema.

        Returns:
            A validated ``ConstraintSet``.

        Raises:
            ConstraintLoadError: If the data does not match the expected
                schema.
        """
        try:
            return ConstraintSet.model_validate(data)
        except ValidationError as exc:
            raise ConstraintLoadError(
                f"Invalid constraint data: {exc}"
            ) from exc
