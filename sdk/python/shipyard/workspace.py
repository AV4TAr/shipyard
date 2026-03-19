"""Workspace — high-level helper for agents working in git worktrees.

When a task is claimed from a project with ``repo_url`` set, the server
creates a git worktree and returns its path in the ``TaskAssignment``.
This class wraps that directory with convenient read/write/run helpers so
agent code doesn't need to deal with file I/O directly.

Example::

    client = ShipyardClient(...)
    task = client.claim_task(task_id)

    if task.worktree_path:
        ws = Workspace(task.worktree_path)
        ws.write("src/auth.py", new_code)
        result = ws.run("pytest tests/test_auth.py -x")
        if result.returncode == 0:
            client.submit_work(task_id=task.task_id, description="Fixed auth")
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, List, Optional


class RunResult:
    """Result of running a command in the workspace."""

    def __init__(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def __repr__(self) -> str:
        status = "ok" if self.success else "fail"
        return "RunResult({}, lines={})".format(status, self.stdout.count("\n"))


class Workspace:
    """Agent workspace backed by a git worktree directory.

    Args:
        path: Absolute path to the worktree directory (from ``TaskAssignment.worktree_path``).
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(
                "Workspace path does not exist: {}".format(path)
            )

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def read(self, relative_path: str) -> str:
        """Read a file from the workspace.

        Args:
            relative_path: Path relative to the workspace root.

        Returns:
            File contents as a string.
        """
        target = self.path / relative_path
        return target.read_text()

    def write(self, relative_path: str, content: str) -> Path:
        """Write content to a file in the workspace.

        Creates parent directories as needed.

        Args:
            relative_path: Path relative to the workspace root.
            content: File content to write.

        Returns:
            Absolute path to the written file.
        """
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def exists(self, relative_path: str) -> bool:
        """Check if a file or directory exists in the workspace."""
        return (self.path / relative_path).exists()

    def list_files(self, pattern: str = "**/*") -> List[str]:
        """List files in the workspace matching a glob pattern.

        Args:
            pattern: Glob pattern (default: all files recursively).

        Returns:
            List of paths relative to the workspace root.
        """
        return [
            str(p.relative_to(self.path))
            for p in self.path.glob(pattern)
            if p.is_file() and ".git" not in p.parts
        ]

    def delete(self, relative_path: str) -> bool:
        """Delete a file from the workspace.

        Returns True if the file was deleted, False if it didn't exist.
        """
        target = self.path / relative_path
        if target.exists():
            target.unlink()
            return True
        return False

    def mkdir(self, relative_path: str) -> Path:
        """Create a directory in the workspace.

        Creates parent directories as needed.
        """
        target = self.path / relative_path
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def run(
        self,
        command: str,
        timeout: int = 120,
        env: Optional[dict[str, str]] = None,
    ) -> RunResult:
        """Run a shell command in the workspace directory.

        Args:
            command: Command string (split on spaces).
            timeout: Timeout in seconds.
            env: Extra environment variables to set.

        Returns:
            A :class:`RunResult` with returncode, stdout, and stderr.
        """
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        try:
            result = subprocess.run(
                command.split(),
                cwd=self.path,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
            )
            return RunResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                returncode=-1,
                stdout="",
                stderr="Command timed out after {}s".format(timeout),
            )

    def run_tests(self, command: str = "pytest", timeout: int = 120) -> RunResult:
        """Convenience wrapper for running tests.

        Args:
            command: Test command (default: ``"pytest"``).
            timeout: Timeout in seconds.
        """
        return self.run(command, timeout=timeout)

    # ------------------------------------------------------------------
    # Git operations
    # ------------------------------------------------------------------

    def git(self, *args: str) -> RunResult:
        """Run a git command in the workspace.

        Args:
            *args: Git subcommand and arguments.

        Returns:
            A :class:`RunResult`.
        """
        cmd = "git " + " ".join(args)
        return self.run(cmd)

    def diff(self) -> str:
        """Get the unified diff of all uncommitted changes."""
        self.git("add", "-A")
        result = self.git("diff", "--cached")
        return result.stdout

    def changed_files(self) -> List[str]:
        """Get the list of changed files."""
        self.git("add", "-A")
        result = self.git("diff", "--cached", "--name-only")
        return [f for f in result.stdout.strip().split("\n") if f]

    def commit(self, message: str) -> Optional[str]:
        """Stage all changes and commit.

        Returns the commit hash, or None if nothing to commit.
        """
        self.git("add", "-A")
        # Use subprocess directly so the message isn't split on spaces
        try:
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return None
            hash_result = self.git("rev-parse", "HEAD")
            return hash_result.stdout.strip() if hash_result.success else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return "Workspace({!r})".format(str(self.path))

    def __str__(self) -> str:
        return str(self.path)
