"""Pluggable sandbox backends — Strategy pattern.

Defines a ``SandboxBackend`` protocol and two implementations:

- ``SimulatedBackend``: Extracted from the original ``SandboxManager`` simulation logic.
- ``OpenSandboxBackend``: Wraps Alibaba's OpenSandbox sync SDK.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from .models import (
    ResourceUsage,
    SandboxConfig,
    SandboxResult,
    SandboxStatus,
    TestResults,
)

# ------------------------------------------------------------------
# Protocol
# ------------------------------------------------------------------


@runtime_checkable
class SandboxBackend(Protocol):
    """Interface that all sandbox backends must satisfy."""

    def create(self, config: SandboxConfig) -> uuid.UUID: ...

    def execute(self, sandbox_id: uuid.UUID, command: str) -> SandboxResult: ...

    def execute_with_test_results(
        self,
        sandbox_id: uuid.UUID,
        command: str,
        *,
        test_results: TestResults,
        status: Optional[SandboxStatus] = None,
    ) -> SandboxResult: ...

    def destroy(self, sandbox_id: uuid.UUID) -> None: ...

    def get_status(self, sandbox_id: uuid.UUID) -> SandboxStatus: ...


# ------------------------------------------------------------------
# Simulated backend (extracted from original SandboxManager)
# ------------------------------------------------------------------


@dataclass
class _SandboxState:
    """Internal bookkeeping for a single sandbox."""

    sandbox_id: uuid.UUID
    config: SandboxConfig
    status: SandboxStatus = SandboxStatus.CREATING
    created_at: float = field(default_factory=time.monotonic)


class SimulatedBackend:
    """In-memory simulation — no Docker or Kubernetes required.

    This is the original ``SandboxManager`` logic, extracted verbatim so
    that all existing tests keep passing.
    """

    def __init__(self) -> None:
        self._sandboxes: dict[uuid.UUID, _SandboxState] = {}

    def create(self, config: SandboxConfig) -> uuid.UUID:
        sandbox_id = uuid.uuid4()
        state = _SandboxState(sandbox_id=sandbox_id, config=config)
        state.status = SandboxStatus.READY
        self._sandboxes[sandbox_id] = state
        return sandbox_id

    def execute(self, sandbox_id: uuid.UUID, command: str) -> SandboxResult:
        state = self._get_state(sandbox_id)
        state.status = SandboxStatus.RUNNING

        start = time.monotonic()
        elapsed = time.monotonic() - start

        timed_out = elapsed > state.config.timeout_seconds

        if timed_out:
            state.status = SandboxStatus.TIMEOUT
        else:
            state.status = SandboxStatus.SUCCEEDED

        return SandboxResult(
            sandbox_id=sandbox_id,
            intent_id=state.config.intent_id,
            status=state.status,
            logs=f"[simulated] executed: {command}",
            test_results=None,
            duration_seconds=elapsed,
            resource_usage=ResourceUsage(),
        )

    def execute_with_test_results(
        self,
        sandbox_id: uuid.UUID,
        command: str,
        *,
        test_results: TestResults,
        status: Optional[SandboxStatus] = None,
    ) -> SandboxResult:
        state = self._get_state(sandbox_id)
        state.status = SandboxStatus.RUNNING

        start = time.monotonic()
        elapsed = time.monotonic() - start

        resolved_status = status or (
            SandboxStatus.SUCCEEDED if test_results.failed == 0 else SandboxStatus.FAILED
        )
        state.status = resolved_status

        return SandboxResult(
            sandbox_id=sandbox_id,
            intent_id=state.config.intent_id,
            status=resolved_status,
            logs=f"[simulated] executed: {command}",
            test_results=test_results,
            duration_seconds=elapsed,
            resource_usage=ResourceUsage(),
        )

    def destroy(self, sandbox_id: uuid.UUID) -> None:
        state = self._get_state(sandbox_id)
        state.status = SandboxStatus.DESTROYED

    def get_status(self, sandbox_id: uuid.UUID) -> SandboxStatus:
        return self._get_state(sandbox_id).status

    def _get_state(self, sandbox_id: uuid.UUID) -> _SandboxState:
        try:
            return self._sandboxes[sandbox_id]
        except KeyError:
            raise KeyError(f"Unknown sandbox: {sandbox_id}") from None


# ------------------------------------------------------------------
# OpenSandbox backend
# ------------------------------------------------------------------


class OpenSandboxBackend:
    """Wraps Alibaba's OpenSandbox sync SDK for real container execution.

    Requires ``pip install opensandbox`` (or ``pip install -e ".[opensandbox]"``).
    The ``opensandbox`` package is lazily imported so that the rest of the
    codebase works without it installed.
    """

    def __init__(self, *, server_url: str | None = None) -> None:
        import os

        # Lazy import — only needed when this backend is actually used.
        try:
            from opensandbox import SandboxSync  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "opensandbox package is required for OpenSandboxBackend. "
                "Install it with: pip install opensandbox"
            ) from exc

        self._SandboxSync = SandboxSync
        self._server_url = server_url or os.environ.get("OPENSANDBOX_SERVER_URL", "")
        self._sandboxes: dict[uuid.UUID, OpenSandboxBackend._OpenSandboxEntry] = {}

    # -- helpers --

    @dataclass
    class _OpenSandboxEntry:
        sandbox_id: uuid.UUID
        config: SandboxConfig
        handle: object  # SandboxSync instance
        status: SandboxStatus = SandboxStatus.READY

    def _map_resources(self, config: SandboxConfig) -> dict:
        """Convert ``ResourceLimits`` to OpenSandbox resource dict."""
        limits = config.resource_limits
        return {
            "cpu": str(int(limits.max_cpu)) if limits.max_cpu == int(limits.max_cpu)
            else str(limits.max_cpu),
            "memory": f"{limits.max_memory_mb}Mi",
        }

    # -- protocol methods --

    def create(self, config: SandboxConfig) -> uuid.UUID:
        sandbox_id = uuid.uuid4()
        resources = self._map_resources(config)
        handle = self._SandboxSync.create(
            image=config.image,
            timeout=config.timeout_seconds,
            envs=config.env_vars or None,
            resource=resources,
        )
        entry = OpenSandboxBackend._OpenSandboxEntry(
            sandbox_id=sandbox_id,
            config=config,
            handle=handle,
            status=SandboxStatus.READY,
        )
        self._sandboxes[sandbox_id] = entry
        return sandbox_id

    def execute(self, sandbox_id: uuid.UUID, command: str) -> SandboxResult:
        entry = self._get_entry(sandbox_id)
        entry.status = SandboxStatus.RUNNING

        start = time.monotonic()
        result = entry.handle.commands.run(command)  # type: ignore[union-attr]
        elapsed = time.monotonic() - start

        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        logs = stdout + ("\n" + stderr if stderr else "")
        exit_code = getattr(result, "exit_code", -1)

        timed_out = elapsed > entry.config.timeout_seconds
        if timed_out:
            entry.status = SandboxStatus.TIMEOUT
        elif exit_code == 0:
            entry.status = SandboxStatus.SUCCEEDED
        else:
            entry.status = SandboxStatus.FAILED

        # Attempt to parse test results from output
        test_results = None
        if "pytest" in command or "py.test" in command:
            from .parser import parse_pytest_output

            test_results = parse_pytest_output(logs)

        return SandboxResult(
            sandbox_id=sandbox_id,
            intent_id=entry.config.intent_id,
            status=entry.status,
            logs=logs,
            test_results=test_results,
            duration_seconds=elapsed,
            resource_usage=ResourceUsage(),
        )

    def execute_with_test_results(
        self,
        sandbox_id: uuid.UUID,
        command: str,
        *,
        test_results: TestResults,
        status: Optional[SandboxStatus] = None,
    ) -> SandboxResult:
        entry = self._get_entry(sandbox_id)
        entry.status = SandboxStatus.RUNNING

        start = time.monotonic()
        result = entry.handle.commands.run(command)  # type: ignore[union-attr]
        elapsed = time.monotonic() - start

        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        logs = stdout + ("\n" + stderr if stderr else "")

        resolved_status = status or (
            SandboxStatus.SUCCEEDED if test_results.failed == 0 else SandboxStatus.FAILED
        )
        entry.status = resolved_status

        return SandboxResult(
            sandbox_id=sandbox_id,
            intent_id=entry.config.intent_id,
            status=resolved_status,
            logs=logs,
            test_results=test_results,
            duration_seconds=elapsed,
            resource_usage=ResourceUsage(),
        )

    def destroy(self, sandbox_id: uuid.UUID) -> None:
        entry = self._get_entry(sandbox_id)
        try:
            entry.handle.kill()  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            entry.handle.close()  # type: ignore[union-attr]
        except Exception:
            pass
        entry.status = SandboxStatus.DESTROYED

    def get_status(self, sandbox_id: uuid.UUID) -> SandboxStatus:
        return self._get_entry(sandbox_id).status

    def _get_entry(self, sandbox_id: uuid.UUID) -> _OpenSandboxEntry:
        try:
            return self._sandboxes[sandbox_id]
        except KeyError:
            raise KeyError(f"Unknown sandbox: {sandbox_id}") from None
