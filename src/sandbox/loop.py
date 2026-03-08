"""Sandbox iteration loop — the core agent feedback cycle.

Run tests -> inspect results -> return structured feedback (or success).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from .manager import SandboxManager
from .models import (
    SandboxConfig,
    SandboxResult,
    SandboxStatus,
    TestFailure,
    TestResults,
)

logger = logging.getLogger(__name__)


@dataclass
class IterationRecord:
    """Snapshot of a single loop iteration."""

    iteration: int
    result: SandboxResult


class SandboxLoop:
    """Implements the agent iteration loop over an ephemeral sandbox.

    Workflow per iteration:
    1. Run the test command inside the sandbox.
    2. Inspect the :class:`SandboxResult`.
    3. If all tests pass -> return success.
    4. If tests fail  -> return structured feedback so the agent can fix & retry.
    5. If max iterations reached -> return the last (failed) result.

    Parameters:
        manager: The :class:`SandboxManager` that owns sandbox lifecycles.
                 If *None*, a fresh manager is created internally.
        test_command: Shell command executed each iteration (default: ``pytest``).
    """

    def __init__(
        self,
        *,
        manager: Optional[SandboxManager] = None,
        test_command: str = "pytest",
        test_results_provider: Optional["_TestResultsProvider"] = None,
    ) -> None:
        self._manager = manager or SandboxManager()
        self._test_command = test_command
        self._history: list[IterationRecord] = []
        self._test_results_provider = test_results_provider or _default_provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, config: SandboxConfig, *, max_iterations: int = 5) -> SandboxResult:
        """Execute the iteration loop and return the final result.

        Args:
            config: Sandbox configuration (image, limits, etc.).
            max_iterations: Upper bound on how many test-fix cycles to attempt.

        Returns:
            The :class:`SandboxResult` from the last iteration.
        """
        sandbox_id = self._manager.create(config)

        try:
            for iteration in range(1, max_iterations + 1):
                logger.info("Iteration %d/%d for sandbox %s", iteration, max_iterations, sandbox_id)

                test_results = self._test_results_provider(iteration, max_iterations)

                result = self._manager.execute_with_test_results(
                    sandbox_id,
                    self._test_command,
                    test_results=test_results,
                )

                self._history.append(IterationRecord(iteration=iteration, result=result))

                if result.status == SandboxStatus.SUCCEEDED:
                    logger.info("All tests passed on iteration %d", iteration)
                    return result

                logger.info(
                    "Iteration %d failed: %d/%d tests passed",
                    iteration,
                    test_results.passed,
                    test_results.total,
                )

            # Exhausted iterations — return the last failure.
            return self._history[-1].result
        finally:
            self._manager.destroy(sandbox_id)

    @property
    def history(self) -> list[IterationRecord]:
        """Iteration records collected during the most recent :meth:`run`."""
        return list(self._history)


# ------------------------------------------------------------------
# Test-results provider (pluggable for testing / simulation)
# ------------------------------------------------------------------

_TestResultsProvider = Callable[[int, int], TestResults]


def _default_provider(iteration: int, max_iterations: int) -> TestResults:
    """Simulated provider: fails on early iterations, passes on the last.

    In a real system this would be replaced by actual test-output parsing.
    """
    if iteration < max_iterations:
        return TestResults(
            total=10,
            passed=7,
            failed=3,
            skipped=0,
            failures=[
                TestFailure(
                    test_name=f"test_example_{i}",
                    message=f"Simulated failure #{i} on iteration {iteration}",
                    structured_error={
                        "type": "AssertionError",
                        "file": f"tests/test_example.py::test_example_{i}",
                        "line": 42 + i,
                        "iteration": iteration,
                    },
                )
                for i in range(1, 4)
            ],
        )

    return TestResults(total=10, passed=10, failed=0, skipped=0, failures=[])
