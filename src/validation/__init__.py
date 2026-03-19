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
from .real_runners import (
    RealBehavioralDiffRunner,
    RealResourceBoundsRunner,
    RealSecurityScanRunner,
    RealStaticAnalysisRunner,
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
    "RealBehavioralDiffRunner",
    "RealResourceBoundsRunner",
    "RealSecurityScanRunner",
    "RealStaticAnalysisRunner",
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
