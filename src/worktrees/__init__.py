"""Git worktree management for agent code workflows.

Each task gets an isolated git worktree so agents can write real files
without affecting the main branch or other agents' work.
"""

from .manager import WorktreeManager

__all__ = ["WorktreeManager"]
