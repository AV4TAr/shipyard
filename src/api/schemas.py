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


class ProjectCreateRequest(BaseModel):
    """Request body for creating a project."""

    title: str
    description: str
    constraints: list[str] = Field(default_factory=list)
    priority: str = "medium"
    target_services: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    repo_url: Optional[str] = None
    default_branch: Optional[str] = None


class ProjectUpdateRequest(BaseModel):
    """Request body for updating a project."""

    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    repo_url: Optional[str] = None
    default_branch: Optional[str] = None


class MilestoneCreateRequest(BaseModel):
    """Request body for adding a milestone to a project."""

    title: str
    description: str = ""
    order: Optional[int] = None
    acceptance_criteria: list[str] = Field(default_factory=list)
