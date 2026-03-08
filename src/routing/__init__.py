"""Agent Selection & Routing System.

Routes tasks to the best specialist agent, falling back to a generic agent
when no specialist is available.
"""

from .analyzer import TaskAnalyzer
from .integration import RoutingBridge
from .models import (
    AgentCapability,
    AgentRegistration,
    AgentStatus,
    RouteDecision,
    RoutingStrategy,
    TaskComplexity,
    TaskRequirements,
)
from .registry import AgentRegistry
from .router import TaskRouter

__all__ = [
    "AgentCapability",
    "AgentRegistration",
    "AgentRegistry",
    "AgentStatus",
    "RouteDecision",
    "RoutingBridge",
    "RoutingStrategy",
    "TaskAnalyzer",
    "TaskComplexity",
    "TaskRequirements",
    "TaskRouter",
]
