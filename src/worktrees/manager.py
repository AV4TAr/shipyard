"""Git worktree manager for isolated agent code workflows."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Base directories for cloned repos and worktrees (absolute to avoid cwd issues)
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_REPOS_DIR = _DATA_DIR / "repos"
_WORKTREES_DIR = _DATA_DIR / "worktrees"


def _slugify(text: str, max_length: int = 40) -> str:
    """Convert text to a git-branch-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length]


def _run_git(
    *args: str,
    cwd: str | Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with timeout."""
    cmd = ["git"] + list(args)
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class WorktreeManager:
    """Manages git repos and worktrees for agent task isolation.

    Lifecycle:
        1. ensure_repo(project) — clone or fetch the project's git repo
        2. create_worktree(project, task) — create an isolated worktree + branch
        3. get_diff(worktree_path) — get the diff of changes in the worktree
        4. merge(project, task) — merge the task branch into main
        5. cleanup(worktree_path) — remove the worktree and branch

    Parameters:
        repos_dir: Directory for bare/cloned repos.
        worktrees_dir: Directory for task worktrees.
    """

    def __init__(
        self,
        repos_dir: Path | None = None,
        worktrees_dir: Path | None = None,
    ) -> None:
        self.repos_dir = repos_dir or _REPOS_DIR
        self.worktrees_dir = worktrees_dir or _WORKTREES_DIR

    def ensure_repo(self, project: Any) -> Path:
        """Clone or fetch the project's git repo.

        Returns the path to the local repo clone.
        """
        if not project.repo_url:
            raise ValueError(f"Project {project.project_id} has no repo_url")

        repo_dir = self.repos_dir / str(project.project_id)

        if repo_dir.exists() and (repo_dir / ".git").exists():
            # Already cloned — fetch latest
            result = _run_git("fetch", "--all", cwd=repo_dir)
            if result.returncode != 0:
                logger.warning("git fetch failed: %s", result.stderr)
        else:
            # Clone fresh
            repo_dir.mkdir(parents=True, exist_ok=True)
            result = _run_git(
                "clone", project.repo_url, str(repo_dir), timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"git clone failed: {result.stderr}"
                )

        # Update project's local path
        project.repo_local_path = str(repo_dir)
        return repo_dir

    def create_worktree(self, project: Any, task: Any) -> dict[str, str]:
        """Create a git worktree for a task.

        Returns a dict with 'worktree_path' and 'branch_name'.
        """
        repo_dir = Path(
            project.repo_local_path
            if project.repo_local_path
            else self.repos_dir / str(project.project_id)
        )

        if not repo_dir.exists():
            repo_dir = self.ensure_repo(project)

        # Prune stale worktree references from previous runs
        _run_git("worktree", "prune", cwd=repo_dir)

        task_id_short = str(task.task_id)[:8]
        title_slug = _slugify(task.title)
        branch_name = f"task/{task_id_short}-{title_slug}"

        worktree_path = (
            self.worktrees_dir
            / str(project.project_id)
            / str(task.task_id)
        )
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # Create the worktree with a new branch
        default_branch = getattr(project, "default_branch", "main")
        result = _run_git(
            "worktree", "add",
            "-b", branch_name,
            str(worktree_path),
            default_branch,
            cwd=repo_dir,
        )

        if result.returncode != 0:
            # Branch may already exist — try without -b
            result = _run_git(
                "worktree", "add",
                str(worktree_path),
                branch_name,
                cwd=repo_dir,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"git worktree add failed: {result.stderr}"
                )

        logger.info(
            "Created worktree for task %s at %s (branch %s)",
            task.task_id,
            worktree_path,
            branch_name,
        )

        return {
            "worktree_path": str(worktree_path),
            "branch_name": branch_name,
        }

    def get_diff(self, worktree_path: str) -> str:
        """Get the unified diff of all changes in a worktree."""
        wt = Path(worktree_path)
        if not wt.exists():
            return ""

        # Stage everything first to capture new files
        _run_git("add", "-A", cwd=wt)

        result = _run_git("diff", "--cached", cwd=wt)
        if result.returncode != 0:
            logger.warning("git diff failed: %s", result.stderr)
            return ""
        return result.stdout

    def get_changed_files(self, worktree_path: str) -> list[str]:
        """Get the list of changed files in a worktree."""
        wt = Path(worktree_path)
        if not wt.exists():
            return []

        _run_git("add", "-A", cwd=wt)
        result = _run_git("diff", "--cached", "--name-only", cwd=wt)
        if result.returncode != 0:
            return []
        return [f for f in result.stdout.strip().split("\n") if f]

    def commit(self, worktree_path: str, message: str) -> str | None:
        """Commit staged changes in a worktree. Returns commit hash or None."""
        wt = Path(worktree_path)
        _run_git("add", "-A", cwd=wt)
        result = _run_git("commit", "-m", message, cwd=wt)
        if result.returncode != 0:
            logger.warning("git commit failed: %s", result.stderr)
            return None
        # Extract commit hash
        hash_result = _run_git("rev-parse", "HEAD", cwd=wt)
        return hash_result.stdout.strip() if hash_result.returncode == 0 else None

    def merge(self, project: Any, task: Any) -> bool:
        """Merge the task branch into the project's default branch.

        Returns True on success.
        """
        repo_dir = Path(
            project.repo_local_path
            if project.repo_local_path
            else self.repos_dir / str(project.project_id)
        )
        branch_name = task.branch_name
        if not branch_name:
            logger.error("Task %s has no branch_name", task.task_id)
            return False

        default_branch = getattr(project, "default_branch", "main")

        # Checkout main
        result = _run_git("checkout", default_branch, cwd=repo_dir)
        if result.returncode != 0:
            logger.error("Failed to checkout %s: %s", default_branch, result.stderr)
            return False

        # Merge
        result = _run_git(
            "merge", "--no-ff", branch_name,
            "-m", f"Merge {branch_name} (task {str(task.task_id)[:8]})",
            cwd=repo_dir,
        )
        if result.returncode != 0:
            logger.error("Merge failed: %s", result.stderr)
            return False

        logger.info("Merged %s into %s", branch_name, default_branch)
        return True

    def cleanup(self, worktree_path: str, repo_dir: str | None = None) -> None:
        """Remove a worktree and optionally its branch."""
        wt = Path(worktree_path)

        if repo_dir:
            # Remove via git worktree remove
            result = _run_git(
                "worktree", "remove", str(wt), "--force",
                cwd=repo_dir,
            )
            if result.returncode != 0:
                logger.warning("git worktree remove failed: %s", result.stderr)

        # Fallback: remove directory
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)

        logger.info("Cleaned up worktree at %s", worktree_path)

    def run_tests(
        self,
        worktree_path: str,
        test_command: str = "python3 -m pytest -p no:asyncio",
        timeout: int = 120,
    ) -> dict[str, Any]:
        """Run tests in a worktree directory.

        Returns a dict with 'returncode', 'stdout', 'stderr', 'passed'.
        """
        wt = Path(worktree_path)
        if not wt.exists():
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": f"Worktree not found: {worktree_path}",
                "passed": False,
            }

        try:
            # Auto-detect src/ layout and set PYTHONPATH
            import os
            env = os.environ.copy()
            src_dir = wt / "src"
            if src_dir.is_dir():
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = str(src_dir) + (
                    os.pathsep + existing if existing else ""
                )

            result = subprocess.run(
                test_command.split(),
                cwd=wt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "passed": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Test timed out after {timeout}s",
                "passed": False,
            }
        except Exception as exc:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
                "passed": False,
            }
