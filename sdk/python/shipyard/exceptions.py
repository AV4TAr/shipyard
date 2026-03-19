"""Shipyard SDK exceptions.

All exceptions raised by the SDK inherit from :class:`ShipyardError` so
callers can catch a single base class if they prefer broad handling.
"""


class ShipyardError(Exception):
    """Base exception for all Shipyard SDK errors."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class ConnectionError(ShipyardError):
    """Raised when the SDK cannot reach the Shipyard server."""

    def __init__(self, message: str = "Cannot connect to Shipyard server") -> None:
        super().__init__(message, status_code=0)


class TaskNotFoundError(ShipyardError):
    """Raised when the requested task does not exist."""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task {task_id} not found", status_code=404)
        self.task_id = task_id


class ClaimFailedError(ShipyardError):
    """Raised when a task claim is rejected (already assigned, etc.)."""

    def __init__(self, task_id: str, reason: str = "Claim rejected") -> None:
        super().__init__(f"Failed to claim task {task_id}: {reason}", status_code=409)
        self.task_id = task_id


class PipelineFailedError(ShipyardError):
    """Raised when the pipeline rejects submitted work."""

    def __init__(
        self,
        task_id: str,
        message: str = "Pipeline rejected submission",
        feedback: "Optional[FeedbackMessage]" = None,  # noqa: F821
    ) -> None:
        super().__init__(f"Pipeline failed for task {task_id}: {message}", status_code=422)
        self.task_id = task_id
        self.feedback = feedback


class RegistrationError(ShipyardError):
    """Raised when agent registration fails after all retries."""

    def __init__(self, message: str = "Agent registration failed") -> None:
        super().__init__(message, status_code=500)
