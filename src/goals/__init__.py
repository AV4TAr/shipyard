"""Goals System — the human's primary interface for telling agents WHAT to build."""

from .bridge import GoalPipelineBridge
from .decomposer import GoalDecomposer
from .manager import GoalManager
from .models import (
    AgentTask,
    Goal,
    GoalInput,
    GoalPriority,
    GoalStatus,
    TaskBreakdown,
    TaskStatus,
)

__all__ = [
    "AgentTask",
    "Goal",
    "GoalInput",
    "GoalPipelineBridge",
    "GoalDecomposer",
    "GoalManager",
    "GoalPriority",
    "GoalStatus",
    "TaskBreakdown",
    "TaskStatus",
]
