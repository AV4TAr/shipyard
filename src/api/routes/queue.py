"""Deploy queue endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.cli.runtime import CLIRuntime

from ..dependencies import get_runtime

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("")
def list_queue(runtime: CLIRuntime = Depends(get_runtime)):
    """List deploy queue entries."""
    entries = runtime.list_queue()
    return [e.model_dump(mode="json") if hasattr(e, "model_dump") else e for e in entries]
