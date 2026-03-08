"""Sandbox Execution Layer — ephemeral environments for agent iteration."""

from .backends import OpenSandboxBackend, SandboxBackend, SimulatedBackend
from .loop import IterationRecord, SandboxLoop
from .manager import SandboxManager
from .models import (
    ResourceLimits,
    ResourceUsage,
    SandboxConfig,
    SandboxResult,
    SandboxStatus,
    TestFailure,
    TestResults,
)
from .parser import parse_pytest_output

__all__ = [
    "IterationRecord",
    "OpenSandboxBackend",
    "ResourceLimits",
    "ResourceUsage",
    "SandboxBackend",
    "SandboxConfig",
    "SandboxLoop",
    "SandboxManager",
    "SandboxResult",
    "SandboxStatus",
    "SimulatedBackend",
    "TestFailure",
    "TestResults",
    "parse_pytest_output",
]
