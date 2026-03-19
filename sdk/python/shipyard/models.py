"""Shipyard SDK data models.

Plain dataclasses that mirror the server-side Pydantic protocol models so
SDK users do not need the server's dependencies installed.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentRegistration:
    """Registration payload sent when an agent connects to Shipyard."""

    agent_id: str
    name: str
    capabilities: List[str]
    languages: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    max_concurrent_tasks: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "capabilities": self.capabilities,
            "languages": self.languages,
            "frameworks": self.frameworks,
            "max_concurrent_tasks": self.max_concurrent_tasks,
        }


@dataclass
class TaskAssignment:
    """A task available for an agent to work on.

    Attributes:
        task_id: Unique task identifier (UUID string).
        goal_id: Parent goal identifier (UUID string).
        title: Human-readable task title.
        description: Detailed description of what needs to be done.
        constraints: Architectural constraints the work must respect.
        acceptance_criteria: Criteria the work must satisfy.
        target_files: Suggested files to modify (may be empty).
        estimated_risk: Risk level -- ``"low"``, ``"medium"``, ``"high"``, or ``"critical"``.
    """

    task_id: str
    goal_id: str
    title: str
    description: str
    constraints: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    target_files: List[str] = field(default_factory=list)
    estimated_risk: str = "medium"

    # Lease fields
    lease_expires_at: Optional[str] = None
    lease_duration_seconds: Optional[int] = None
    heartbeat_interval_seconds: Optional[int] = None

    # Worktree fields
    worktree_path: Optional[str] = None
    branch_name: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskAssignment":
        """Create a TaskAssignment from a JSON response dict."""
        return cls(
            task_id=str(data["task_id"]),
            goal_id=str(data["goal_id"]),
            title=data["title"],
            description=data["description"],
            constraints=data.get("constraints", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            target_files=data.get("target_files", []),
            estimated_risk=data.get("estimated_risk", "medium"),
            lease_expires_at=data.get("lease_expires_at"),
            lease_duration_seconds=data.get("lease_duration_seconds"),
            heartbeat_interval_seconds=data.get("heartbeat_interval_seconds"),
            worktree_path=data.get("worktree_path"),
            branch_name=data.get("branch_name"),
        )


@dataclass
class FeedbackMessage:
    """Structured feedback returned after submitting work to the pipeline.

    Attributes:
        task_id: The task this feedback relates to.
        status: One of ``"accepted"``, ``"rejected"``, or ``"needs_revision"``.
        message: Human-readable summary.
        details: Extra metadata (e.g. ``{"run_id": "..."}``).
        suggestions: Actionable next-step suggestions from the pipeline.
        validation_results: Raw validation signal results.
    """

    task_id: str
    status: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)
    validation_results: Dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        """Return ``True`` if the pipeline accepted the work."""
        return self.status == "accepted"

    @property
    def rejected(self) -> bool:
        """Return ``True`` if the pipeline rejected the work."""
        return self.status == "rejected"

    @property
    def needs_revision(self) -> bool:
        """Return ``True`` if the work is pending human review."""
        return self.status == "needs_revision"

    @property
    def run_id(self) -> Optional[str]:
        """Return the pipeline run ID if available."""
        return self.details.get("run_id")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FeedbackMessage":
        """Create a FeedbackMessage from a JSON response dict."""
        return cls(
            task_id=str(data["task_id"]),
            status=data["status"],
            message=data["message"],
            details=data.get("details", {}),
            suggestions=data.get("suggestions", []),
            validation_results=data.get("validation_results", {}),
        )
