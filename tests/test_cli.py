"""Tests for the Human CLI — argument parsing, formatting, and runtime wiring."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from src.cli.app import build_parser, main
from src.cli.formatters import (
    format_agent,
    format_goal,
    format_goal_with_tasks,
    format_run,
    format_status_dashboard,
    format_table,
)
from src.cli.runtime import CLIRuntime
from src.goals.models import Goal, GoalPriority, GoalStatus, AgentTask, TaskStatus
from src.intent.schema import RiskLevel
from src.pipeline.models import (
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageResult,
)
from src.trust.models import AgentProfile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable ANSI colours in all tests for deterministic output."""
    monkeypatch.setenv("NO_COLOR", "1")


@pytest.fixture()
def parser() -> "argparse.ArgumentParser":
    return build_parser()


@pytest.fixture()
def sample_goal() -> Goal:
    return Goal(
        goal_id=uuid.UUID("12345678-1234-1234-1234-123456789abc"),
        title="Add rate limiting",
        description="Add rate limiting to all public API endpoints",
        constraints=["must use Redis", "max 50ms latency impact"],
        acceptance_criteria=["all public endpoints have rate limits", "returns 429 on excess"],
        priority=GoalPriority.HIGH,
        target_services=["api"],
        created_by="cli-user",
        created_at=datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        status=GoalStatus.DRAFT,
    )


@pytest.fixture()
def sample_run() -> PipelineRun:
    run = PipelineRun(
        run_id=uuid.UUID("abcdef01-abcd-abcd-abcd-abcdef012345"),
        intent_id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
        agent_id="agent-alpha",
        current_stage=PipelineStage.VALIDATION,
        status=PipelineStatus.IN_PROGRESS,
        started_at=datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
    )
    run.record_stage(
        StageResult(
            stage=PipelineStage.INTENT,
            status=PipelineStatus.PASSED,
            duration_seconds=0.12,
        )
    )
    return run


@pytest.fixture()
def sample_agent() -> AgentProfile:
    return AgentProfile(
        agent_id="agent-alpha",
        total_deployments=50,
        successful_deployments=47,
        rollbacks=2,
        avg_risk_score=0.35,
        created_at=datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture()
def sample_tasks(sample_goal: Goal) -> list[AgentTask]:
    return [
        AgentTask(
            goal_id=sample_goal.goal_id,
            title="Implement: Add rate limiting",
            description="Implement the changes",
            target_services=["api"],
            constraints=["must use Redis"],
            estimated_risk=RiskLevel.MEDIUM,
            status=TaskStatus.IN_PROGRESS,
        ),
        AgentTask(
            goal_id=sample_goal.goal_id,
            title="Test: Add rate limiting",
            description="Write and run tests",
            target_services=["api"],
            estimated_risk=RiskLevel.LOW,
            status=TaskStatus.PENDING,
        ),
    ]


# ===========================================================================
# Argument parsing tests
# ===========================================================================


class TestGoalParsing:
    """Test CLI argument parsing for goal subcommands."""

    def test_goal_create_minimal(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "goal", "create",
            "--title", "Add caching",
            "--description", "Add Redis caching layer",
        ])
        assert args.command == "goal"
        assert args.goal_action == "create"
        assert args.title == "Add caching"
        assert args.description == "Add Redis caching layer"
        assert args.constraints == []
        assert args.criteria == []
        assert args.priority == "medium"
        assert args.services == []

    def test_goal_create_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "goal", "create",
            "--title", "Add rate limiting",
            "--description", "Add rate limiting to all public API endpoints",
            "--constraints", "must use Redis", "max 50ms latency impact",
            "--criteria", "all public endpoints have rate limits", "returns 429 on excess",
            "--priority", "high",
            "--services", "api",
        ])
        assert args.title == "Add rate limiting"
        assert args.constraints == ["must use Redis", "max 50ms latency impact"]
        assert args.criteria == ["all public endpoints have rate limits", "returns 429 on excess"]
        assert args.priority == "high"
        assert args.services == ["api"]

    def test_goal_list_no_filters(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["goal", "list"])
        assert args.goal_action == "list"
        assert args.status is None
        assert args.priority is None

    def test_goal_list_with_filters(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["goal", "list", "--status", "active", "--priority", "high"])
        assert args.status == "active"
        assert args.priority == "high"

    def test_goal_activate(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["goal", "activate", "12345678-1234-1234-1234-123456789abc"])
        assert args.goal_action == "activate"
        assert args.goal_id == "12345678-1234-1234-1234-123456789abc"

    def test_goal_show(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["goal", "show", "12345678-1234-1234-1234-123456789abc"])
        assert args.goal_action == "show"
        assert args.goal_id == "12345678-1234-1234-1234-123456789abc"

    def test_goal_cancel(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["goal", "cancel", "12345678-1234-1234-1234-123456789abc"])
        assert args.goal_action == "cancel"
        assert args.goal_id == "12345678-1234-1234-1234-123456789abc"


class TestStatusParsing:
    def test_status(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"


class TestApproveParsing:
    def test_approve_minimal(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["approve", "abcdef01-abcd-abcd-abcd-abcdef012345"])
        assert args.command == "approve"
        assert args.run_id == "abcdef01-abcd-abcd-abcd-abcdef012345"
        assert args.comment is None

    def test_approve_with_comment(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "approve", "abcdef01-abcd-abcd-abcd-abcdef012345",
            "--comment", "looks good",
        ])
        assert args.comment == "looks good"


class TestRejectParsing:
    def test_reject(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "reject", "abcdef01-abcd-abcd-abcd-abcdef012345",
            "--reason", "needs error handling for edge case X",
        ])
        assert args.command == "reject"
        assert args.reason == "needs error handling for edge case X"

    def test_reject_requires_reason(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["reject", "some-id"])


class TestAgentsParsing:
    def test_agents_list(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["agents"])
        assert args.command == "agents"
        assert args.agent is None

    def test_agents_detail(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["agents", "--agent", "agent-alpha"])
        assert args.agent == "agent-alpha"


class TestRunsParsing:
    def test_runs_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["runs"])
        assert args.command == "runs"
        assert args.agent is None
        assert args.status is None
        assert args.limit == 20

    def test_runs_with_filters(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "runs", "--agent", "agent-alpha", "--status", "failed", "--limit", "5",
        ])
        assert args.agent == "agent-alpha"
        assert args.status == "failed"
        assert args.limit == 5


class TestQueueParsing:
    def test_queue(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["queue"])
        assert args.command == "queue"


class TestConstraintsParsing:
    def test_constraints_show(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["constraints", "show"])
        assert args.command == "constraints"
        assert args.constraints_action == "show"

    def test_constraints_check(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["constraints", "check", "src/api/handler.py"])
        assert args.constraints_action == "check"
        assert args.file == "src/api/handler.py"


# ===========================================================================
# Formatting tests
# ===========================================================================


class TestFormatTable:
    def test_basic_table(self) -> None:
        result = format_table(["Name", "Age"], [["Alice", "30"], ["Bob", "25"]])
        lines = result.split("\n")
        assert len(lines) == 4  # header + separator + 2 rows
        assert "NAME" in lines[0]
        assert "AGE" in lines[0]
        assert "---" in lines[1]
        assert "Alice" in lines[2]
        assert "Bob" in lines[3]

    def test_empty_headers(self) -> None:
        assert format_table([], []) == ""

    def test_uneven_rows(self) -> None:
        result = format_table(["A", "B", "C"], [["1"]])
        lines = result.split("\n")
        assert len(lines) == 3
        # Short row should be padded
        assert "1" in lines[2]

    def test_column_width_adapts(self) -> None:
        result = format_table(["X"], [["a-very-long-value"]])
        lines = result.split("\n")
        # The separator should be at least as wide as the longest value
        assert len(lines[1].strip()) >= len("a-very-long-value")


class TestFormatGoal:
    def test_contains_key_fields(self, sample_goal: Goal) -> None:
        result = format_goal(sample_goal)
        assert "Add rate limiting" in result
        assert "12345678" in result
        assert "draft" in result
        assert "high" in result
        assert "must use Redis" in result
        assert "all public endpoints have rate limits" in result
        assert "api" in result

    def test_format_goal_with_tasks(
        self, sample_goal: Goal, sample_tasks: list[AgentTask]
    ) -> None:
        result = format_goal_with_tasks(sample_goal, sample_tasks)
        assert "Tasks (2)" in result
        assert "Implement: Add rate limiting" in result
        assert "Test: Add rate limiting" in result

    def test_format_goal_no_tasks(self, sample_goal: Goal) -> None:
        result = format_goal_with_tasks(sample_goal, [])
        assert "No tasks yet" in result


class TestFormatRun:
    def test_contains_key_fields(self, sample_run: PipelineRun) -> None:
        result = format_run(sample_run)
        assert "abcdef01" in result
        assert "agent-alpha" in result
        assert "in_progress" in result
        assert "Stage Results" in result
        assert "intent" in result


class TestFormatAgent:
    def test_contains_key_fields(self, sample_agent: AgentProfile) -> None:
        result = format_agent(sample_agent)
        assert "agent-alpha" in result
        assert "50" in result  # total_deployments
        assert "47" in result  # successful
        assert "2" in result   # rollbacks


class TestFormatStatusDashboard:
    def test_dashboard_output(self) -> None:
        data = {
            "active_goals": 3,
            "pipeline_runs_in_progress": 1,
            "pending_approvals": 2,
            "agent_count": 5,
            "active_agents": 3,
            "deploy_queue_length": 1,
        }
        result = format_status_dashboard(data)
        assert "Shipyard Status Dashboard" in result
        assert "3" in result   # active goals
        assert "Goals" in result
        assert "Pipeline" in result
        assert "Agents" in result
        assert "Deploy Queue" in result

    def test_dashboard_zeros(self) -> None:
        data = {
            "active_goals": 0,
            "pipeline_runs_in_progress": 0,
            "pending_approvals": 0,
            "agent_count": 0,
            "active_agents": 0,
            "deploy_queue_length": 0,
        }
        result = format_status_dashboard(data)
        assert "Shipyard Status Dashboard" in result


# ===========================================================================
# Runtime wiring tests
# ===========================================================================


class TestRuntime:
    def test_from_defaults_creates_valid_runtime(self) -> None:
        runtime = CLIRuntime.from_defaults()
        assert runtime.goal_manager is not None
        assert runtime.orchestrator is not None
        assert runtime.trust_tracker is not None
        assert runtime.claim_manager is not None
        assert runtime.deploy_queue is not None
        assert runtime.intent_registry is not None

    def test_create_and_list_goal(self) -> None:
        runtime = CLIRuntime.from_defaults()
        goal = runtime.create_goal(
            title="Test goal",
            description="A test goal",
            priority="high",
        )
        assert goal.title == "Test goal"
        assert goal.priority.value == "high"
        assert goal.status.value == "draft"

        goals = runtime.list_goals()
        assert len(goals) == 1
        assert goals[0].goal_id == goal.goal_id

    def test_activate_goal(self) -> None:
        runtime = CLIRuntime.from_defaults()
        goal = runtime.create_goal(title="Activate me", description="desc")
        breakdown = runtime.activate_goal(str(goal.goal_id))
        assert len(breakdown.tasks) >= 2  # at least impl + test

    def test_cancel_goal(self) -> None:
        runtime = CLIRuntime.from_defaults()
        goal = runtime.create_goal(title="Cancel me", description="desc")
        cancelled = runtime.cancel_goal(str(goal.goal_id))
        assert cancelled.status.value == "cancelled"

    def test_show_goal(self) -> None:
        runtime = CLIRuntime.from_defaults()
        goal = runtime.create_goal(title="Show me", description="desc")
        shown_goal, tasks = runtime.show_goal(str(goal.goal_id))
        assert shown_goal.goal_id == goal.goal_id
        assert tasks == []  # not activated yet

    def test_status_data(self) -> None:
        runtime = CLIRuntime.from_defaults()
        data = runtime.get_status_data()
        assert "active_goals" in data
        assert "pipeline_runs_in_progress" in data
        assert "pending_approvals" in data
        assert "agent_count" in data
        assert "active_agents" in data
        assert "deploy_queue_length" in data

    def test_list_goals_with_filters(self) -> None:
        runtime = CLIRuntime.from_defaults()
        runtime.create_goal(title="High", description="d", priority="high")
        runtime.create_goal(title="Low", description="d", priority="low")

        high_goals = runtime.list_goals(priority="high")
        assert len(high_goals) == 1
        assert high_goals[0].title == "High"

    def test_list_agents_empty(self) -> None:
        runtime = CLIRuntime.from_defaults()
        agents = runtime.list_agents()
        assert agents == []

    def test_get_agent_creates_profile(self) -> None:
        runtime = CLIRuntime.from_defaults()
        profile = runtime.get_agent("new-agent")
        assert profile.agent_id == "new-agent"
        assert profile.total_deployments == 0

    def test_list_runs_empty(self) -> None:
        runtime = CLIRuntime.from_defaults()
        runs = runtime.list_runs()
        assert runs == []

    def test_list_queue_empty(self) -> None:
        runtime = CLIRuntime.from_defaults()
        entries = runtime.list_queue()
        assert entries == []

    def test_from_config_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            CLIRuntime.from_config("/nonexistent/config.json")


# ===========================================================================
# Main entry integration (parsing only, no real execution)
# ===========================================================================


class TestMainEntry:
    def test_no_args_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = main([])
        assert result == 0

    def test_status_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = main(["status"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Dashboard" in captured.out

    def test_goal_create_and_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Create uses a fresh runtime each call, so we cannot test cross-command
        # state. We just verify the command runs without error.
        result = main([
            "goal", "create",
            "--title", "Test",
            "--description", "A test",
        ])
        assert result == 0
        captured = capsys.readouterr()
        assert "Goal created" in captured.out
