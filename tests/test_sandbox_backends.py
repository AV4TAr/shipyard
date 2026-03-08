"""Tests for sandbox backends."""

from __future__ import annotations

import uuid

import pytest

from src.sandbox.backends import SandboxBackend, SimulatedBackend
from src.sandbox.models import (
    SandboxConfig,
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


# ------------------------------------------------------------------
# SimulatedBackend — must match original SandboxManager behaviour
# ------------------------------------------------------------------


class TestSimulatedBackend:
    def test_implements_protocol(self):
        assert isinstance(SimulatedBackend(), SandboxBackend)

    def test_create_returns_uuid(self):
        backend = SimulatedBackend()
        sid = backend.create(_make_config())
        assert isinstance(sid, uuid.UUID)

    def test_status_after_create_is_ready(self):
        backend = SimulatedBackend()
        sid = backend.create(_make_config())
        assert backend.get_status(sid) == SandboxStatus.READY

    def test_execute_returns_succeeded(self):
        backend = SimulatedBackend()
        cfg = _make_config()
        sid = backend.create(cfg)
        result = backend.execute(sid, "echo hello")
        assert isinstance(result, SandboxResult)
        assert result.status == SandboxStatus.SUCCEEDED
        assert result.sandbox_id == sid
        assert result.intent_id == cfg.intent_id
        assert "echo hello" in result.logs

    def test_execute_with_test_results_failed(self):
        backend = SimulatedBackend()
        sid = backend.create(_make_config())
        tr = TestResults(
            total=3,
            passed=1,
            failed=2,
            failures=[
                TestFailure(test_name="test_foo", message="boom"),
            ],
        )
        result = backend.execute_with_test_results(sid, "pytest", test_results=tr)
        assert result.status == SandboxStatus.FAILED
        assert result.test_results is not None
        assert result.test_results.failed == 2

    def test_execute_with_test_results_success(self):
        backend = SimulatedBackend()
        sid = backend.create(_make_config())
        tr = TestResults(total=3, passed=3, failed=0)
        result = backend.execute_with_test_results(sid, "pytest", test_results=tr)
        assert result.status == SandboxStatus.SUCCEEDED

    def test_execute_with_test_results_explicit_status(self):
        backend = SimulatedBackend()
        sid = backend.create(_make_config())
        tr = TestResults(total=3, passed=3, failed=0)
        result = backend.execute_with_test_results(
            sid, "pytest", test_results=tr, status=SandboxStatus.FAILED,
        )
        assert result.status == SandboxStatus.FAILED

    def test_destroy_sets_destroyed(self):
        backend = SimulatedBackend()
        sid = backend.create(_make_config())
        backend.destroy(sid)
        assert backend.get_status(sid) == SandboxStatus.DESTROYED

    def test_unknown_sandbox_raises(self):
        backend = SimulatedBackend()
        with pytest.raises(KeyError, match="Unknown sandbox"):
            backend.get_status(uuid.uuid4())

    def test_destroy_unknown_raises(self):
        backend = SimulatedBackend()
        with pytest.raises(KeyError):
            backend.destroy(uuid.uuid4())


# ------------------------------------------------------------------
# OpenSandboxBackend — guarded import test
# ------------------------------------------------------------------


class TestOpenSandboxBackendImport:
    def test_import_error_without_opensandbox(self):
        """OpenSandboxBackend should raise ImportError if opensandbox is not installed."""
        try:
            import opensandbox  # noqa: F401

            pytest.skip("opensandbox is installed — cannot test import guard")
        except ImportError:
            pass

        from src.sandbox.backends import OpenSandboxBackend

        with pytest.raises(ImportError, match="opensandbox package is required"):
            OpenSandboxBackend()
