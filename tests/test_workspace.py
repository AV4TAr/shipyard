"""Tests for the SDK Workspace helper."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def workspace_dir(tmp_path):
    """Create a git-initialized workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=ws, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=ws, capture_output=True,
    )
    (ws / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, capture_output=True)
    return ws


@pytest.fixture
def ws(workspace_dir):
    """Create a Workspace instance."""
    # Import here so the test file doesn't fail if sdk isn't on path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))
    from shipyard.workspace import Workspace
    return Workspace(str(workspace_dir))


class TestWorkspaceFileOps:
    def test_write_and_read(self, ws):
        ws.write("src/main.py", "print('hello')\n")
        assert ws.read("src/main.py") == "print('hello')\n"

    def test_exists(self, ws):
        assert ws.exists("README.md")
        assert not ws.exists("nonexistent.py")

    def test_write_creates_parents(self, ws):
        ws.write("deep/nested/dir/file.txt", "content")
        assert ws.exists("deep/nested/dir/file.txt")

    def test_list_files(self, ws):
        ws.write("a.py", "x")
        ws.write("b.py", "y")
        files = ws.list_files("*.py")
        assert "a.py" in files
        assert "b.py" in files

    def test_delete(self, ws):
        ws.write("temp.py", "x")
        assert ws.delete("temp.py") is True
        assert ws.exists("temp.py") is False
        assert ws.delete("temp.py") is False

    def test_mkdir(self, ws):
        ws.mkdir("new_dir/sub")
        assert ws.exists("new_dir/sub")


class TestWorkspaceRun:
    def test_run_success(self, ws):
        result = ws.run("echo hello")
        assert result.success
        assert "hello" in result.stdout

    def test_run_failure(self, ws):
        result = ws.run("python3 -c \"import sys; sys.exit(1)\"")
        assert not result.success
        assert result.returncode == 1

    def test_run_tests_passing(self, ws):
        ws.write("test_ok.py", "def test_ok(): assert True\n")
        result = ws.run_tests("python3 -m pytest test_ok.py -x")
        assert result.success

    def test_run_tests_failing(self, ws):
        ws.write("test_bad.py", "def test_bad(): assert False\n")
        result = ws.run_tests("python3 -m pytest test_bad.py -x")
        assert not result.success

    def test_run_repr(self, ws):
        result = ws.run("echo test")
        assert "RunResult" in repr(result)


class TestWorkspaceGit:
    def test_diff_shows_changes(self, ws):
        ws.write("new.py", "# new file\n")
        diff = ws.diff()
        assert "new.py" in diff
        assert "# new file" in diff

    def test_changed_files(self, ws):
        ws.write("foo.py", "x = 1\n")
        ws.write("bar.py", "y = 2\n")
        files = ws.changed_files()
        assert "foo.py" in files
        assert "bar.py" in files

    def test_commit(self, ws):
        ws.write("feature.py", "# feature\n")
        sha = ws.commit("Add feature")
        assert sha is not None
        assert len(sha) == 40

    def test_commit_nothing(self, ws):
        sha = ws.commit("Empty")
        assert sha is None


class TestWorkspaceInit:
    def test_nonexistent_path_raises(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))
        from shipyard.workspace import Workspace
        with pytest.raises(FileNotFoundError):
            Workspace("/nonexistent/path")

    def test_repr(self, ws):
        assert "Workspace" in repr(ws)

    def test_str(self, ws):
        assert str(ws.path) == str(ws)
