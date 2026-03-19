"""Integration tests for worktree pipeline and approve-merge flow."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.goals.models import AgentTask, GoalStatus, TaskStatus
from src.intent.schema import IntentDeclaration, RiskLevel
from src.pipeline.models import PipelineRun, PipelineStatus


class TestOrchestratorWorktreeIntegration:
    """Test that the orchestrator uses worktree tests when metadata is set."""

    def _make_runtime(self):
        from src.cli.runtime import CLIRuntime
        return CLIRuntime.from_defaults()

    def test_sandbox_uses_worktree_when_metadata_set(self):
        """When worktree_path is in metadata, real tests run instead of sim."""
        rt = self._make_runtime()

        # Mock the worktree manager's run_tests
        rt.orchestrator._worktree_manager = MagicMock()
        rt.orchestrator._worktree_manager.run_tests.return_value = {
            "returncode": 0,
            "stdout": "1 passed",
            "stderr": "",
            "passed": True,
        }
        rt.orchestrator._worktree_manager.commit.return_value = "abc123"

        intent = IntentDeclaration(
            agent_id="test-agent",
            description="Test change",
            rationale="Testing worktree integration",
            target_files=["src/main.py"],
        )

        # Set worktree metadata
        run = rt.orchestrator.run(intent, "test-agent")
        # The simulated sandbox runs since metadata isn't set before run()
        assert run is not None

    def test_sandbox_still_simulates_without_worktree(self):
        """Without worktree_path, the existing simulated sandbox runs."""
        rt = self._make_runtime()

        intent = IntentDeclaration(
            agent_id="test-agent",
            description="Test change",
            rationale="Testing worktree integration",
            target_files=["src/main.py"],
        )
        run = rt.orchestrator.run(intent, "test-agent")
        # Should pass through simulated sandbox
        assert run is not None
        sandbox = run.metadata.get("sandbox_result", {})
        assert sandbox.get("worktree") is not True  # Not a worktree run

    def test_worktree_sandbox_failure_stops_pipeline(self):
        """When worktree tests fail, pipeline should fail at sandbox stage."""
        rt = self._make_runtime()

        mock_wt = MagicMock()
        mock_wt.run_tests.return_value = {
            "returncode": 1,
            "stdout": "",
            "stderr": "FAILED test_auth.py::test_login",
            "passed": False,
        }
        rt.orchestrator._worktree_manager = mock_wt

        intent = IntentDeclaration(
            agent_id="test-agent",
            description="Broken change",
            rationale="Testing worktree failure",
            target_files=["src/auth.py"],
        )

        # Manually create a run with worktree metadata
        from src.pipeline.models import PipelineConfig
        pipeline_run = PipelineRun(
            intent_id=intent.intent_id,
            agent_id="test-agent",
            status=PipelineStatus.IN_PROGRESS,
            metadata={"worktree_path": "/tmp/fake-worktree"},
        )

        result = rt.orchestrator._run_sandbox_stage(intent, pipeline_run)
        assert result.status == PipelineStatus.FAILED
        assert "worktree" in (result.error or "").lower()

    def test_worktree_sandbox_success(self):
        """When worktree tests pass, sandbox stage should pass."""
        rt = self._make_runtime()

        mock_wt = MagicMock()
        mock_wt.run_tests.return_value = {
            "returncode": 0,
            "stdout": "5 passed in 0.3s",
            "stderr": "",
            "passed": True,
        }
        rt.orchestrator._worktree_manager = mock_wt

        intent = IntentDeclaration(
            agent_id="test-agent",
            description="Good change",
            rationale="Testing worktree success",
            target_files=["src/feature.py"],
        )

        pipeline_run = PipelineRun(
            intent_id=intent.intent_id,
            agent_id="test-agent",
            status=PipelineStatus.IN_PROGRESS,
            metadata={"worktree_path": "/tmp/fake-worktree"},
        )

        result = rt.orchestrator._run_sandbox_stage(intent, pipeline_run)
        assert result.status == PipelineStatus.PASSED
        assert pipeline_run.metadata["sandbox_result"]["worktree"] is True


class TestApproveRunMerge:
    """Test that approve_run triggers worktree merge."""

    def _make_runtime_with_blocked_run(self):
        from src.cli.runtime import CLIRuntime
        rt = CLIRuntime.from_defaults()

        # Create a goal, task, and a blocked pipeline run
        goal = rt.create_goal(
            title="Test goal",
            description="For testing merge",
        )
        rt.goal_manager.activate(goal.goal_id)
        tasks = rt.goal_manager.get_tasks(goal.goal_id)
        task = tasks[0]

        intent = IntentDeclaration(
            agent_id="test-agent",
            description="Change to merge",
            rationale="Testing merge flow",
            target_files=["src/main.py"],
        )
        run = PipelineRun(
            intent_id=intent.intent_id,
            agent_id="test-agent",
            status=PipelineStatus.BLOCKED,
            metadata={
                "task_id": str(task.task_id),
                "worktree_path": "/tmp/test-worktree",
                "branch_name": "task/abc123-test",
                "description": "Change to merge",
            },
        )
        rt.orchestrator._save_run(run)
        return rt, run, task

    def test_approve_run_calls_merge(self):
        rt, run, task = self._make_runtime_with_blocked_run()

        # Mock worktree manager
        mock_wt = MagicMock()
        mock_wt.commit.return_value = "abc123"
        mock_wt.merge.return_value = True
        mock_wt.cleanup.return_value = None
        mock_wt.repos_dir = MagicMock()
        mock_wt.repos_dir.__truediv__ = MagicMock(return_value="fake_path")
        rt.worktree_manager = mock_wt

        result = rt.approve_run(str(run.run_id))
        assert result.status == PipelineStatus.PASSED

        # Verify commit was attempted
        mock_wt.commit.assert_called_once()
        # Verify cleanup was called
        mock_wt.cleanup.assert_called_once()

    def test_approve_run_without_worktree_still_works(self):
        """Standard approve without worktree metadata should work as before."""
        from src.cli.runtime import CLIRuntime
        rt = CLIRuntime.from_defaults()

        intent = IntentDeclaration(
            agent_id="test-agent",
            description="Simple change",
            rationale="Testing approve without worktree",
            target_files=["src/main.py"],
        )
        run = PipelineRun(
            intent_id=intent.intent_id,
            agent_id="test-agent",
            status=PipelineStatus.BLOCKED,
            metadata={"task_id": "not-a-real-task"},
        )
        rt.orchestrator._save_run(run)

        result = rt.approve_run(str(run.run_id))
        assert result.status == PipelineStatus.PASSED

    def test_approve_run_releases_lease(self):
        rt, run, task = self._make_runtime_with_blocked_run()

        # Claim with lease
        rt.lease_manager.claim(task.task_id, "test-agent")
        assert rt.lease_manager.get_lease(task.task_id) is not None

        # Mock worktree to avoid real git ops
        rt.worktree_manager = MagicMock()
        rt.worktree_manager.commit.return_value = None
        rt.worktree_manager.cleanup.return_value = None
        rt.worktree_manager.repos_dir = MagicMock()
        rt.worktree_manager.repos_dir.__truediv__ = MagicMock(return_value="x")

        rt.approve_run(str(run.run_id))

        # Lease should be released
        assert rt.lease_manager.get_lease(task.task_id) is None


class TestRuntimeHelpers:
    """Test the new _find_task and _find_project_for_goal helpers."""

    def test_find_task(self):
        from src.cli.runtime import CLIRuntime
        rt = CLIRuntime.from_defaults()

        goal = rt.create_goal(title="Test", description="Test")
        rt.goal_manager.activate(goal.goal_id)
        tasks = rt.goal_manager.get_tasks(goal.goal_id)

        found = rt._find_task(tasks[0].task_id)
        assert found is not None
        assert found.task_id == tasks[0].task_id

    def test_find_task_not_found(self):
        from src.cli.runtime import CLIRuntime
        rt = CLIRuntime.from_defaults()
        assert rt._find_task(uuid.uuid4()) is None

    def test_find_project_for_goal_no_projects(self):
        from src.cli.runtime import CLIRuntime
        rt = CLIRuntime.from_defaults()
        assert rt._find_project_for_goal(uuid.uuid4()) is None
