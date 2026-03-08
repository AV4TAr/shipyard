"""Multi-Signal Validation Framework.

Validates agent work using multiple independent signals before allowing
changes to proceed through the CI/CD pipeline.
"""

from .gate import ValidationGate
from .models import (
    Finding,
    Severity,
    SignalResult,
    ValidationSignal,
    ValidationVerdict,
)
from .signals import (
    BehavioralDiffRunner,
    IntentAlignmentRunner,
    ResourceBoundsRunner,
    SecurityScanRunner,
    StaticAnalysisRunner,
    ValidationSignalRunner,
)

__all__ = [
    "BehavioralDiffRunner",
    "Finding",
    "IntentAlignmentRunner",
    "ResourceBoundsRunner",
    "SecurityScanRunner",
    "Severity",
    "SignalResult",
    "StaticAnalysisRunner",
    "ValidationGate",
    "ValidationSignal",
    "ValidationSignalRunner",
    "ValidationVerdict",
]
