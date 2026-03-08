"""Pydantic request/response schemas for the API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GoalCreateRequest(BaseModel):
    """Request body for creating a goal."""

    title: str
    description: str
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: str = "medium"
    target_services: list[str] = Field(default_factory=list)


class RunApproveRequest(BaseModel):
    """Request body for approving a pipeline run."""

    comment: Optional[str] = None


class RunRejectRequest(BaseModel):
    """Request body for rejecting a pipeline run."""

    reason: str
