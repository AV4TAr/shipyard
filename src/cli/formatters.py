"""Output formatting helpers for the Human CLI.

Provides ASCII tables, pretty-printed models, and a status dashboard.
All output uses ANSI colours when supported, with a no-colour fallback
controlled by the ``NO_COLOR`` environment variable.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_NO_COLOR = bool(os.environ.get("NO_COLOR"))


def _supports_color() -> bool:
    """Return True when stdout is a TTY and NO_COLOR is not set."""
    if _NO_COLOR:
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class _Colors:
    """ANSI escape sequences."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"


class _NoColors:
    """Fallback when colour is disabled — every attribute returns ``""``."""

    def __getattr__(self, _name: str) -> str:
        return ""


def _get_colors() -> _Colors | _NoColors:
    return _Colors() if _supports_color() else _NoColors()


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

_STATUS_COLORS: dict[str, str] = {
    # Pipeline / run statuses
    "passed": "GREEN",
    "completed": "GREEN",
    "failed": "RED",
    "rolled_back": "RED",
    "cancelled": "RED",
    "in_progress": "YELLOW",
    "assigned": "YELLOW",
    "active": "YELLOW",
    "pending": "BLUE",
    "blocked": "BLUE",
    "draft": "DIM",
    # Goal priorities
    "urgent": "RED",
    "high": "YELLOW",
    "medium": "BLUE",
    "low": "DIM",
}


def _colorize_status(status: str) -> str:
    """Return *status* wrapped in the appropriate ANSI colour."""
    c = _get_colors()
    attr_name = _STATUS_COLORS.get(status.lower(), "WHITE")
    colour = getattr(c, attr_name, "")
    return f"{colour}{status}{c.RESET}"


# ---------------------------------------------------------------------------
# Table formatter
# ---------------------------------------------------------------------------


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a simple ASCII table.

    >>> print(format_table(["Name", "Age"], [["Alice", "30"], ["Bob", "25"]]))
    NAME   AGE
    -----  ---
    Alice  30
    Bob    25
    """
    if not headers:
        return ""

    upper_headers = [h.upper() for h in headers]
    col_widths = [len(h) for h in upper_headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(cell))

    def _pad_row(cells: list[str]) -> str:
        parts: list[str] = []
        for i, cell in enumerate(cells):
            width = col_widths[i] if i < len(col_widths) else len(cell)
            parts.append(cell.ljust(width))
        return "  ".join(parts).rstrip()

    c = _get_colors()
    lines: list[str] = []
    lines.append(f"{c.BOLD}{_pad_row(upper_headers)}{c.RESET}")
    lines.append(_pad_row(["-" * w for w in col_widths]))

    for row in rows:
        padded = row + [""] * (len(headers) - len(row))  # pad short rows
        lines.append(_pad_row(padded))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Model formatters
# ---------------------------------------------------------------------------


def format_goal(goal: Any) -> str:
    """Pretty-print a Goal model."""
    c = _get_colors()
    lines: list[str] = []

    lines.append(f"{c.BOLD}Goal: {goal.title}{c.RESET}")
    lines.append(f"  ID:          {goal.goal_id}")
    lines.append(f"  Status:      {_colorize_status(goal.status.value if hasattr(goal.status, 'value') else str(goal.status))}")
    lines.append(f"  Priority:    {_colorize_status(goal.priority.value if hasattr(goal.priority, 'value') else str(goal.priority))}")
    lines.append(f"  Created:     {_format_datetime(goal.created_at)}")
    if goal.created_by:
        lines.append(f"  Created by:  {goal.created_by}")
    lines.append(f"  Description: {goal.description}")

    if goal.constraints:
        lines.append(f"\n  {c.BOLD}Constraints:{c.RESET}")
        for constraint in goal.constraints:
            lines.append(f"    - {constraint}")

    if goal.acceptance_criteria:
        lines.append(f"\n  {c.BOLD}Acceptance Criteria:{c.RESET}")
        for criterion in goal.acceptance_criteria:
            lines.append(f"    - {criterion}")

    if goal.target_services:
        lines.append(f"\n  {c.BOLD}Services:{c.RESET} {', '.join(goal.target_services)}")

    return "\n".join(lines)


def format_goal_with_tasks(goal: Any, tasks: list[Any]) -> str:
    """Pretty-print a Goal along with its decomposed tasks."""
    lines: list[str] = [format_goal(goal)]
    c = _get_colors()

    if tasks:
        lines.append(f"\n  {c.BOLD}Tasks ({len(tasks)}):{c.RESET}")
        for task in tasks:
            status_str = task.status.value if hasattr(task.status, "value") else str(task.status)
            risk_str = task.estimated_risk.value if hasattr(task.estimated_risk, "value") else str(task.estimated_risk)
            lines.append(
                f"    {_colorize_status(status_str):>20s}  "
                f"{task.title}  "
                f"{c.DIM}[risk: {risk_str}]{c.RESET}"
            )
    else:
        lines.append(f"\n  {c.DIM}No tasks yet (goal not activated){c.RESET}")

    return "\n".join(lines)


def format_run(run: Any) -> str:
    """Pretty-print a PipelineRun."""
    c = _get_colors()
    status_val = run.status.value if hasattr(run.status, "value") else str(run.status)
    stage_val = run.current_stage.value if hasattr(run.current_stage, "value") else str(run.current_stage)

    lines: list[str] = []
    lines.append(f"{c.BOLD}Pipeline Run: {run.run_id}{c.RESET}")
    lines.append(f"  Agent:    {run.agent_id}")
    lines.append(f"  Status:   {_colorize_status(status_val)}")
    lines.append(f"  Stage:    {stage_val}")
    lines.append(f"  Started:  {_format_datetime(run.started_at)}")

    if run.completed_at:
        lines.append(f"  Finished: {_format_datetime(run.completed_at)}")

    if run.stage_results:
        lines.append(f"\n  {c.BOLD}Stage Results:{c.RESET}")
        for stage, result in run.stage_results.items():
            stage_name = stage.value if hasattr(stage, "value") else str(stage)
            result_status = result.status.value if hasattr(result.status, "value") else str(result.status)
            dur = f"{result.duration_seconds:.2f}s"
            error_part = f"  {c.RED}{result.error}{c.RESET}" if result.error else ""
            lines.append(
                f"    {stage_name:<16s} {_colorize_status(result_status):<20s} {c.DIM}{dur}{c.RESET}{error_part}"
            )

    return "\n".join(lines)


def format_agent(profile: Any) -> str:
    """Pretty-print an AgentProfile."""
    c = _get_colors()
    lines: list[str] = []

    lines.append(f"{c.BOLD}Agent: {profile.agent_id}{c.RESET}")
    lines.append(f"  Trust Score:    {_format_trust_score(profile.trust_score)}")
    lines.append(f"  Deployments:    {profile.total_deployments}")
    lines.append(f"  Successful:     {profile.successful_deployments}")
    lines.append(f"  Rollbacks:      {profile.rollbacks}")
    lines.append(f"  Success Rate:   {profile.success_rate:.1%}")
    lines.append(f"  Avg Risk Score: {profile.avg_risk_score:.4f}")
    lines.append(f"  Member Since:   {_format_datetime(profile.created_at)}")

    return "\n".join(lines)


def format_status_dashboard(data: dict[str, Any]) -> str:
    """Render the main status overview dashboard.

    Expected *data* keys:
        active_goals, pipeline_runs_in_progress, pending_approvals,
        agent_count, active_agents, deploy_queue_length
    """
    c = _get_colors()
    lines: list[str] = []

    lines.append(f"{c.BOLD}{c.CYAN}{'=' * 50}{c.RESET}")
    lines.append(f"{c.BOLD}{c.CYAN}  AI-CICD Status Dashboard{c.RESET}")
    lines.append(f"{c.BOLD}{c.CYAN}{'=' * 50}{c.RESET}")
    lines.append("")

    active_goals = data.get("active_goals", 0)
    in_progress = data.get("pipeline_runs_in_progress", 0)
    pending = data.get("pending_approvals", 0)
    agent_count = data.get("agent_count", 0)
    active_agents = data.get("active_agents", 0)
    queue_len = data.get("deploy_queue_length", 0)

    lines.append(f"  {c.BOLD}Goals{c.RESET}")
    lines.append(f"    Active:              {_highlight_nonzero(active_goals, c)}")
    lines.append("")
    lines.append(f"  {c.BOLD}Pipeline{c.RESET}")
    lines.append(f"    Runs in progress:    {_highlight_nonzero(in_progress, c)}")
    lines.append(f"    Pending approvals:   {_highlight_pending(pending, c)}")
    lines.append("")
    lines.append(f"  {c.BOLD}Agents{c.RESET}")
    lines.append(f"    Registered:          {agent_count}")
    lines.append(f"    Active claims:       {active_agents}")
    lines.append("")
    lines.append(f"  {c.BOLD}Deploy Queue{c.RESET}")
    lines.append(f"    Queued:              {queue_len}")
    lines.append("")
    lines.append(f"{c.DIM}  Updated: {_format_datetime(datetime.now(timezone.utc))}{c.RESET}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _format_datetime(dt: datetime) -> str:
    """Format a datetime for display."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_trust_score(score: float) -> str:
    """Colour-code a trust score."""
    c = _get_colors()
    formatted = f"{score:.4f}"
    if score >= 0.8:
        return f"{c.GREEN}{formatted}{c.RESET}"
    if score >= 0.5:
        return f"{c.YELLOW}{formatted}{c.RESET}"
    return f"{c.RED}{formatted}{c.RESET}"


def _highlight_nonzero(value: int, c: _Colors | _NoColors) -> str:
    if value > 0:
        return f"{c.GREEN}{value}{c.RESET}"
    return f"{c.DIM}{value}{c.RESET}"


def _highlight_pending(value: int, c: _Colors | _NoColors) -> str:
    if value > 0:
        return f"{c.YELLOW}{value}{c.RESET}"
    return f"{c.DIM}{value}{c.RESET}"
