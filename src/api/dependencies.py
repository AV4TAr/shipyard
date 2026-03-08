"""Shared FastAPI dependencies."""

from __future__ import annotations

from functools import lru_cache

from src.cli.runtime import CLIRuntime


@lru_cache(maxsize=1)
def _get_runtime() -> CLIRuntime:
    """Return a singleton CLIRuntime instance."""
    return CLIRuntime.from_defaults()


def get_runtime() -> CLIRuntime:
    """FastAPI dependency that provides the singleton CLIRuntime."""
    return _get_runtime()
