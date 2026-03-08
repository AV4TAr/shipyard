"""Agent Coordination Layer — manages multiple agents working on the same codebase."""

from .claims import ClaimManager
from .merge import Change, SemanticMergeChecker
from .models import Claim, ClaimConflict, ConflictResolution, MergeCheck
from .queue import DeployQueue, QueueEntry

__all__ = [
    "Change",
    "Claim",
    "ClaimConflict",
    "ClaimManager",
    "ConflictResolution",
    "DeployQueue",
    "MergeCheck",
    "QueueEntry",
    "SemanticMergeChecker",
]
