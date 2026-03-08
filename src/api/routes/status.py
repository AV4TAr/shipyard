"""Status dashboard endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.cli.runtime import CLIRuntime

from ..dependencies import get_runtime

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("")
def get_status(runtime: CLIRuntime = Depends(get_runtime)):
    """Return status dashboard data."""
    return runtime.get_status_data()
