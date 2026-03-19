"""Shipyard Python SDK -- build AI agents for the Shipyard CI/CD pipeline.

Quick start::

    from shipyard import ShipyardClient

    client = ShipyardClient(
        base_url="http://localhost:8001",
        agent_id="agent-mybot",
        name="mybot",
        capabilities=["python"],
    )
    client.register()
    tasks = client.list_tasks()
"""

from .client import ShipyardClient
from .exceptions import (
    ClaimFailedError,
    ConnectionError,
    PipelineFailedError,
    RegistrationError,
    ShipyardError,
    TaskNotFoundError,
)
from .models import AgentRegistration, FeedbackMessage, TaskAssignment
from .workspace import Workspace

__version__ = "0.1.0"

__all__ = [
    "ShipyardClient",
    "Workspace",
    "AgentRegistration",
    "FeedbackMessage",
    "TaskAssignment",
    "ShipyardError",
    "ConnectionError",
    "ClaimFailedError",
    "PipelineFailedError",
    "RegistrationError",
    "TaskNotFoundError",
]
