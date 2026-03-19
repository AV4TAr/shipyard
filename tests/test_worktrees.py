"""Tests for the git worktree manager (Phase 3)."""

import os
import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.worktrees.manager import WorktreeManager, _slugify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path):
    """Create a temporary git repo with an initial commit."""
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_dir, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_dir, capture_output=True,
    )
    # Create initial commit
    (repo_dir / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_dir, capture_output=True,
    )
    return repo_dir


@pytest.fixture
def wt_manager(tmp_path):
    """Create a WorktreeManager with temp directories."""
    return WorktreeManager(
        repos_dir=tmp_path / "repos",
        worktrees_dir=tmp_path / "worktrees",
    )


def _mock_project(repo_dir=None, repo_url=None, project_id=None):
    """Create a mock project with repo info."""
    p = MagicMock()
    p.project_id = project_id or uuid.uuid4()
    p.repo_url = repo_url
    p.repo_local_path = str(repo_dir) if repo_dir else None
    p.default_branch = "main"
    return p


def _mock_task(task_id=None, title="Test task"):
    """Create a mock task."""
    t = MagicMock()
    t.task_id = task_id or uuid.uuid4()
    t.title = title
    t.worktree_path = None
    t.branch_name = None
    return t


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("Fix bug #123!") == "fix-bug-123"

    def test_max_length(self):
        result = _slugify("a" * 100, max_length=20)
        assert len(result) <= 20

    def test_empty(self):
        assert _slugify("") == ""


# ---------------------------------------------------------------------------
# WorktreeManager — create_worktree
# ---------------------------------------------------------------------------


class TestCreateWorktree:
    def test_creates_worktree_directory(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task(title="Add login page")

        result = wt_manager.create_worktree(project, task)

        assert "worktree_path" in result
        assert "branch_name" in result
        assert Path(result["worktree_path"]).exists()
        assert result["branch_name"].startswith("task/")

    def test_branch_name_format(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task(title="Fix Auth Bug")

        result = wt_manager.create_worktree(project, task)
        branch = result["branch_name"]

        assert branch.startswith("task/")
        assert "fix-auth-bug" in branch

    def test_worktree_is_on_correct_branch(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        wt_path = result["worktree_path"]

        # Check the branch in the worktree
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt_path, capture_output=True, text=True,
        )
        assert branch_result.stdout.strip() == result["branch_name"]


# ---------------------------------------------------------------------------
# WorktreeManager — get_diff and get_changed_files
# ---------------------------------------------------------------------------


class TestDiffAndChanges:
    def test_get_diff_with_changes(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        wt_path = result["worktree_path"]

        # Write a file in the worktree
        (Path(wt_path) / "new_file.py").write_text("print('hello')\n")

        diff = wt_manager.get_diff(wt_path)
        assert "new_file.py" in diff
        assert "print('hello')" in diff

    def test_get_diff_no_changes(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        diff = wt_manager.get_diff(result["worktree_path"])
        assert diff == ""

    def test_get_diff_nonexistent_path(self, wt_manager):
        assert wt_manager.get_diff("/nonexistent/path") == ""

    def test_get_changed_files(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        wt_path = result["worktree_path"]

        (Path(wt_path) / "foo.py").write_text("x = 1\n")
        (Path(wt_path) / "bar.py").write_text("y = 2\n")

        files = wt_manager.get_changed_files(wt_path)
        assert "foo.py" in files
        assert "bar.py" in files

    def test_get_changed_files_nonexistent(self, wt_manager):
        assert wt_manager.get_changed_files("/nonexistent") == []


# ---------------------------------------------------------------------------
# WorktreeManager — commit
# ---------------------------------------------------------------------------


class TestCommit:
    def test_commit_returns_hash(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        wt_path = result["worktree_path"]

        # Configure git in worktree
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=wt_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=wt_path, capture_output=True,
        )

        (Path(wt_path) / "feature.py").write_text("# new feature\n")
        commit_hash = wt_manager.commit(wt_path, "Add feature")

        assert commit_hash is not None
        assert len(commit_hash) == 40  # full SHA

    def test_commit_no_changes(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        commit_hash = wt_manager.commit(result["worktree_path"], "Empty")
        assert commit_hash is None  # nothing to commit


# ---------------------------------------------------------------------------
# WorktreeManager — merge
# ---------------------------------------------------------------------------


class TestMerge:
    def test_merge_succeeds(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        wt_path = result["worktree_path"]

        # Configure git
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=wt_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=wt_path, capture_output=True,
        )

        # Make changes and commit
        (Path(wt_path) / "feature.py").write_text("# new\n")
        wt_manager.commit(wt_path, "Add feature")

        # Set branch name on mock task
        task.branch_name = result["branch_name"]

        # Need to remove the worktree before merging (git limitation)
        wt_manager.cleanup(wt_path, str(tmp_git_repo))

        success = wt_manager.merge(project, task)
        assert success is True

        # Verify file exists on main
        assert (tmp_git_repo / "feature.py").exists()

    def test_merge_no_branch_name(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()
        task.branch_name = None

        assert wt_manager.merge(project, task) is False


# ---------------------------------------------------------------------------
# WorktreeManager — cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_removes_directory(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        wt_path = result["worktree_path"]
        assert Path(wt_path).exists()

        wt_manager.cleanup(wt_path, str(tmp_git_repo))
        assert not Path(wt_path).exists()

    def test_cleanup_nonexistent_is_safe(self, wt_manager):
        # Should not raise
        wt_manager.cleanup("/nonexistent/path")


# ---------------------------------------------------------------------------
# WorktreeManager — run_tests
# ---------------------------------------------------------------------------


class TestRunTests:
    def test_run_tests_passing(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        wt_path = result["worktree_path"]

        # Write a trivial test
        (Path(wt_path) / "test_trivial.py").write_text(
            "def test_pass(): assert True\n"
        )

        test_result = wt_manager.run_tests(wt_path, "python3 -m pytest test_trivial.py -x")
        assert test_result["passed"] is True
        assert test_result["returncode"] == 0

    def test_run_tests_failing(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        result = wt_manager.create_worktree(project, task)
        wt_path = result["worktree_path"]

        (Path(wt_path) / "test_fail.py").write_text(
            "def test_fail(): assert False\n"
        )

        test_result = wt_manager.run_tests(wt_path, "python3 -m pytest test_fail.py -x")
        assert test_result["passed"] is False
        assert test_result["returncode"] != 0

    def test_run_tests_nonexistent_path(self, wt_manager):
        result = wt_manager.run_tests("/nonexistent")
        assert result["passed"] is False

    def test_run_tests_timeout(self, tmp_git_repo, wt_manager):
        project = _mock_project(repo_dir=tmp_git_repo)
        task = _mock_task()

        wt_result = wt_manager.create_worktree(project, task)
        wt_path = wt_result["worktree_path"]

        # Write a slow test
        (Path(wt_path) / "test_slow.py").write_text(
            "import time\ndef test_slow(): time.sleep(10)\n"
        )

        result = wt_manager.run_tests(
            wt_path, "python3 -m pytest test_slow.py -x", timeout=1
        )
        assert result["passed"] is False
        assert "timed out" in result["stderr"].lower()


# ---------------------------------------------------------------------------
# Project model fields
# ---------------------------------------------------------------------------


class TestProjectModelFields:
    def test_project_default_repo_fields(self):
        from src.projects.models import Project

        p = Project(title="Test", description="Test project")
        assert p.repo_url is None
        assert p.repo_local_path is None
        assert p.default_branch == "main"

    def test_project_with_repo_fields(self):
        from src.projects.models import Project

        p = Project(
            title="Test",
            description="Test project",
            repo_url="https://github.com/test/repo.git",
            default_branch="develop",
        )
        assert p.repo_url == "https://github.com/test/repo.git"
        assert p.default_branch == "develop"

    def test_project_serialization_roundtrip(self):
        from src.projects.models import Project

        p = Project(
            title="Test",
            description="desc",
            repo_url="https://github.com/test/repo.git",
            repo_local_path="/tmp/repos/test",
            default_branch="main",
        )
        data = p.model_dump(mode="json")
        restored = Project.model_validate(data)
        assert restored.repo_url == "https://github.com/test/repo.git"
        assert restored.repo_local_path == "/tmp/repos/test"
