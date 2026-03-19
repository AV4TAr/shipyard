"""Agent SDK / Protocol for the Shipyard system.

Defines how external AI agents connect, authenticate, pick up tasks,
declare intents, submit work, and receive structured feedback.
"""

from .agent_client import AgentClient
from .protocol import (
    AgentRegistration,
    FeedbackMessage,
    TaskAssignment,
    WorkSubmission,
)

__all__ = [
    "AgentClient",
    "AgentRegistration",
    "FeedbackMessage",
    "TaskAssignment",
    "WorkSubmission",
]
