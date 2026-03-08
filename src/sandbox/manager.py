"""Sandbox lifecycle manager — facade over pluggable backends.

Delegates to a :class:`SandboxBackend` implementation. Defaults to
:class:`SimulatedBackend` for backward compatibility.
"""

from __future__ import annotations

import uuid
from typing import Optional

from .backends import SandboxBackend, SimulatedBackend
from .models import (
    SandboxConfig,
    SandboxResult,
    SandboxStatus,
    TestResults,
)


class SandboxManager:
    """Manages the full lifecycle of ephemeral sandbox environments.

    By default uses :class:`SimulatedBackend` (in-memory, no Docker).
    Pass a backend to use real execution::

        from src.sandbox.backends import OpenSandboxBackend
        mgr = SandboxManager(backend=OpenSandboxBackend())
    """

    def __init__(self, backend: SandboxBackend | None = None) -> None:
        self._backend: SandboxBackend = backend or SimulatedBackend()

    # ------------------------------------------------------------------
    # Public API — delegates to backend
    # ------------------------------------------------------------------

    def create(self, config: SandboxConfig) -> uuid.UUID:
        """Provision a new sandbox and return its id."""
        return self._backend.create(config)

    def execute(self, sandbox_id: uuid.UUID, command: str) -> SandboxResult:
        """Run a command inside the sandbox and return the result.

        Raises:
            KeyError: If the sandbox_id is unknown.
        """
        return self._backend.execute(sandbox_id, command)

    def execute_with_test_results(
        self,
        sandbox_id: uuid.UUID,
        command: str,
        *,
        test_results: TestResults,
        status: Optional[SandboxStatus] = None,
    ) -> SandboxResult:
        """Execute a command and attach pre-built test results.

        This is a convenience helper used by higher-level orchestration (e.g.
        :class:`SandboxLoop`) to inject parsed test output.  In a real system
        the test results would be extracted from the container's output.
        """
        return self._backend.execute_with_test_results(
            sandbox_id, command, test_results=test_results, status=status,
        )

    def destroy(self, sandbox_id: uuid.UUID) -> None:
        """Tear down a sandbox and release its resources.

        Raises:
            KeyError: If the sandbox_id is unknown.
        """
        self._backend.destroy(sandbox_id)

    def get_status(self, sandbox_id: uuid.UUID) -> SandboxStatus:
        """Return the current status of the sandbox.

        Raises:
            KeyError: If the sandbox_id is unknown.
        """
        return self._backend.get_status(sandbox_id)
