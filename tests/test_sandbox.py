"""Tests for the Sandbox Execution Layer."""

from __future__ import annotations

import uuid

import pytest

from src.sandbox import (
    ResourceLimits,
    SandboxConfig,
    SandboxLoop,
    SandboxManager,
    SandboxResult,
    SandboxStatus,
    TestFailure,
    TestResults,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_config(**overrides) -> SandboxConfig:
    defaults = {"intent_id": uuid.uuid4()}
    defaults.update(overrides)
    return SandboxConfig(**defaults)


def _always_pass(_iteration: int, _max: int) -> TestResults:
    return TestResults(total=5, passed=5, failed=0, skipped=0)


def _always_fail(_iteration: int, _max: int) -> TestResults:
    return TestResults(
        total=5,
        passed=2,
        failed=3,
        skipped=0,
        failures=[
            TestFailure(
                test_name="test_a",
                message="boom",
                structured_error={"type": "AssertionError", "line": 10},
            ),
        ],
    )


def _pass_on_third(iteration: int, _max: int) -> TestResults:
    if iteration < 3:
        return TestResults(
            total=4,
            passed=2,
            failed=2,
            skipped=0,
            failures=[
                TestFailure(
                    test_name="test_x",
                    message=f"fail iter {iteration}",
                    structured_error={"iteration": iteration},
                ),
            ],
        )
    return TestResults(total=4, passed=4, failed=0, skipped=0)


# ------------------------------------------------------------------
# Sandbox creation & lifecycle
# ------------------------------------------------------------------


class TestSandboxManager:
    def test_create_returns_uuid(self):
        mgr = SandboxManager()
        sid = mgr.create(_make_config())
        assert isinstance(sid, uuid.UUID)

    def test_status_after_create_is_ready(self):
        mgr = SandboxManager()
        sid = mgr.create(_make_config())
        assert mgr.get_status(sid) == SandboxStatus.READY

    def test_destroy_sets_status(self):
        mgr = SandboxManager()
        sid = mgr.create(_make_config())
        mgr.destroy(sid)
        assert mgr.get_status(sid) == SandboxStatus.DESTROYED

    def test_get_status_unknown_raises(self):
        mgr = SandboxManager()
        with pytest.raises(KeyError, match="Unknown sandbox"):
            mgr.get_status(uuid.uuid4())

    def test_destroy_unknown_raises(self):
        mgr = SandboxManager()
        with pytest.raises(KeyError):
            mgr.destroy(uuid.uuid4())


# ------------------------------------------------------------------
# Execute returns proper results
# ------------------------------------------------------------------


class TestSandboxExecute:
    def test_execute_returns_sandbox_result(self):
        mgr = SandboxManager()
        cfg = _make_config()
        sid = mgr.create(cfg)
        result = mgr.execute(sid, "echo hello")

        assert isinstance(result, SandboxResult)
        assert result.sandbox_id == sid
        assert result.intent_id == cfg.intent_id
        assert result.status == SandboxStatus.SUCCEEDED
        assert result.duration_seconds >= 0.0
        assert "echo hello" in result.logs

    def test_execute_with_test_results_failed(self):
        mgr = SandboxManager()
        sid = mgr.create(_make_config())
        tr = TestResults(
            total=3,
            passed=1,
            failed=2,
            skipped=0,
            failures=[
                TestFailure(
                    test_name="test_foo",
                    message="expected 1 got 2",
                    structured_error={"type": "AssertionError"},
                ),
            ],
        )
        result = mgr.execute_with_test_results(sid, "pytest", test_results=tr)
        assert result.status == SandboxStatus.FAILED
        assert result.test_results is not None
        assert result.test_results.failed == 2
        assert len(result.test_results.failures) == 1

    def test_execute_with_test_results_success(self):
        mgr = SandboxManager()
        sid = mgr.create(_make_config())
        tr = TestResults(total=3, passed=3, failed=0, skipped=0)
        result = mgr.execute_with_test_results(sid, "pytest", test_results=tr)
        assert result.status == SandboxStatus.SUCCEEDED

    def test_status_transitions_during_execute(self):
        mgr = SandboxManager()
        sid = mgr.create(_make_config())
        assert mgr.get_status(sid) == SandboxStatus.READY
        mgr.execute(sid, "ls")
        assert mgr.get_status(sid) == SandboxStatus.SUCCEEDED


# ------------------------------------------------------------------
# SandboxLoop respects max_iterations
# ------------------------------------------------------------------


class TestSandboxLoop:
    def test_loop_succeeds_immediately(self):
        loop = SandboxLoop(test_results_provider=_always_pass)
        result = loop.run(_make_config(), max_iterations=5)
        assert result.status == SandboxStatus.SUCCEEDED
        assert len(loop.history) == 1

    def test_loop_exhausts_max_iterations(self):
        loop = SandboxLoop(test_results_provider=_always_fail)
        result = loop.run(_make_config(), max_iterations=3)
        assert result.status == SandboxStatus.FAILED
        assert len(loop.history) == 3

    def test_loop_passes_on_third_iteration(self):
        loop = SandboxLoop(test_results_provider=_pass_on_third)
        result = loop.run(_make_config(), max_iterations=5)
        assert result.status == SandboxStatus.SUCCEEDED
        assert len(loop.history) == 3

    def test_loop_single_iteration_fail(self):
        loop = SandboxLoop(test_results_provider=_always_fail)
        result = loop.run(_make_config(), max_iterations=1)
        assert result.status == SandboxStatus.FAILED
        assert len(loop.history) == 1

    def test_loop_history_tracks_iterations(self):
        loop = SandboxLoop(test_results_provider=_pass_on_third)
        loop.run(_make_config(), max_iterations=5)
        iterations = [r.iteration for r in loop.history]
        assert iterations == [1, 2, 3]

    def test_loop_destroys_sandbox(self):
        mgr = SandboxManager()
        loop = SandboxLoop(manager=mgr, test_results_provider=_always_pass)
        result = loop.run(_make_config(), max_iterations=1)
        assert mgr.get_status(result.sandbox_id) == SandboxStatus.DESTROYED


# ------------------------------------------------------------------
# Resource limits are tracked
# ------------------------------------------------------------------


class TestResourceLimits:
    def test_default_resource_limits(self):
        limits = ResourceLimits()
        assert limits.max_cpu == 1.0
        assert limits.max_memory_mb == 512
        assert limits.max_disk_mb == 1024

    def test_custom_resource_limits(self):
        limits = ResourceLimits(max_cpu=4.0, max_memory_mb=2048, max_disk_mb=4096)
        assert limits.max_cpu == 4.0
        assert limits.max_memory_mb == 2048

    def test_config_carries_resource_limits(self):
        limits = ResourceLimits(max_cpu=2.0, max_memory_mb=1024, max_disk_mb=2048)
        cfg = _make_config(resource_limits=limits)
        assert cfg.resource_limits.max_cpu == 2.0

    def test_result_contains_resource_usage(self):
        mgr = SandboxManager()
        sid = mgr.create(_make_config())
        result = mgr.execute(sid, "echo hi")
        assert result.resource_usage.peak_cpu == 0.0
        assert result.resource_usage.peak_memory_mb == 0
        assert result.resource_usage.disk_used_mb == 0


# ------------------------------------------------------------------
# Timeout handling
# ------------------------------------------------------------------


class TestTimeoutHandling:
    def test_config_default_timeout(self):
        cfg = _make_config()
        assert cfg.timeout_seconds == 300

    def test_config_custom_timeout(self):
        cfg = _make_config(timeout_seconds=60)
        assert cfg.timeout_seconds == 60

    def test_config_timeout_must_be_positive(self):
        with pytest.raises(ValueError):
            _make_config(timeout_seconds=0)

    def test_timeout_status_on_result(self):
        """Verify the TIMEOUT status can be set and round-trips through the model."""
        result = SandboxResult(
            sandbox_id=uuid.uuid4(),
            intent_id=uuid.uuid4(),
            status=SandboxStatus.TIMEOUT,
            logs="Process killed after timeout",
            duration_seconds=300.0,
        )
        assert result.status == SandboxStatus.TIMEOUT
