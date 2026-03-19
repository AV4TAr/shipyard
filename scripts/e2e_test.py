#!/usr/bin/env python3
"""End-to-end pipeline test with real git worktrees and real validation.

Demonstrates the full Shipyard pipeline:
  1. Create a project pointing at a real git repo (data/test-repo/)
  2. Add milestones and goals
  3. Activate the project (triggers cascade: milestones -> goals -> tasks)
  4. Simulate an agent: claim task, write fix, submit work
  5. Pipeline runs all 5 stages with real validation (ruff, bandit, behavioral diff)
  6. Approve if needed, merge into main
  7. Verify the fix is on main

Usage:
    python3 scripts/e2e_test.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

# Ensure we can import from the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# Also add the SDK to the path
sys.path.insert(0, str(PROJECT_ROOT / "sdk" / "python"))

# ---------------------------------------------------------------------------
# ANSI helpers (no dependencies)
# ---------------------------------------------------------------------------

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}[PASS]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {CYAN}[INFO]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}[WARN]{RESET} {msg}")


def header(msg: str) -> None:
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  {msg}{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}")


def subheader(msg: str) -> None:
    print(f"\n{BOLD}--- {msg} ---{RESET}")


# ---------------------------------------------------------------------------
# Hardcoded agent fixes (the point is to test infrastructure, not agent coding)
# ---------------------------------------------------------------------------

FORMATTER_FIX = '''\
"""Number formatting utilities."""


def format_number(n: float, decimals: int = 2) -> str:
    """Format a number to the given number of decimal places."""
    return f"{n:.{decimals}f}"


def format_percentage(value: float, total: float) -> str:
    """Format value/total as a percentage string."""
    if total == 0:
        return "0.00%"
    return f"{(value / total) * 100:.2f}%"


def format_ratio(a: float, b: float) -> str:
    """Format a ratio a/b as a decimal string.

    Returns 'undefined (division by zero)' when b is zero.
    """
    if b == 0:
        return "undefined (division by zero)"
    return f"{a / b:.2f}"
'''

CALCULATOR_POWER = '''\
"""Basic calculator functions."""


def add(a: float, b: float) -> float:
    """Return the sum of a and b."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Return the difference a - b."""
    return a - b


def multiply(a: float, b: float) -> float:
    """Return the product of a and b."""
    return a * b


def divide(a: float, b: float) -> float:
    """Return the quotient a / b.

    Raises:
        ZeroDivisionError: If b is zero.
    """
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero")
    return a / b


def power(base: float, exponent: float) -> float:
    """Return base raised to the power of exponent."""
    return base ** exponent
'''

POWER_TESTS = '''\
"""Tests for the power function in calculator."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mathlib.calculator import power


def test_power_basic():
    assert power(2, 3) == 8


def test_power_zero_exponent():
    assert power(5, 0) == 1


def test_power_negative_exponent():
    assert power(2, -1) == 0.5


def test_power_fractional():
    assert abs(power(4, 0.5) - 2.0) < 1e-9
'''

# ---------------------------------------------------------------------------
# Test repo setup
# ---------------------------------------------------------------------------

TEST_REPO_PATH = PROJECT_ROOT / "data" / "test-repo"


def ensure_test_repo() -> Path:
    """Make sure the test repo exists with a clean main branch at the initial commit.

    If the repo has been modified by a previous e2e run, it is reset to the
    initial commit so the test is fully repeatable.
    """
    if not TEST_REPO_PATH.exists():
        print(f"{RED}Test repo not found at {TEST_REPO_PATH}{RESET}")
        print("Run this script from the project root after creating the test repo.")
        sys.exit(1)

    # Prune any leftover worktrees
    subprocess.run(
        ["git", "worktree", "prune"], cwd=TEST_REPO_PATH,
        capture_output=True, text=True,
    )

    # Make sure we're on main
    subprocess.run(
        ["git", "checkout", "main"], cwd=TEST_REPO_PATH,
        capture_output=True, text=True,
    )

    # Find the initial commits (the ones before any e2e merge commits)
    # We want to reset to the commit that has .gitignore but still has the bug.
    log_result = subprocess.run(
        ["git", "log", "--oneline", "--reverse"],
        cwd=TEST_REPO_PATH, capture_output=True, text=True,
    )
    lines = [l.strip() for l in log_result.stdout.strip().splitlines() if l.strip()]

    # Find the last "setup" commit (before any task merges)
    reset_to = None
    for line in lines:
        commit_hash = line.split()[0]
        msg = " ".join(line.split()[1:])
        if "Merge task/" in msg or "Deploy:" in msg:
            break
        reset_to = commit_hash

    if reset_to:
        current = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=TEST_REPO_PATH, capture_output=True, text=True,
        ).stdout.strip()
        if current != reset_to:
            subprocess.run(
                ["git", "reset", "--hard", reset_to],
                cwd=TEST_REPO_PATH, capture_output=True, text=True,
            )
            info(f"Reset test repo to {reset_to} (removed previous e2e artifacts)")

    subprocess.run(
        ["git", "clean", "-fd"], cwd=TEST_REPO_PATH,
        capture_output=True, text=True,
    )

    # Delete any leftover task branches
    branch_result = subprocess.run(
        ["git", "branch"], cwd=TEST_REPO_PATH,
        capture_output=True, text=True,
    )
    for line in branch_result.stdout.splitlines():
        branch = line.strip().lstrip("* ")
        if branch.startswith("task/"):
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=TEST_REPO_PATH, capture_output=True, text=True,
            )

    # Verify the failing test exists
    result = subprocess.run(
        ["python3", "-m", "pytest", "tests/", "-v", "--tb=no"],
        cwd=TEST_REPO_PATH, capture_output=True, text=True,
    )
    if "1 failed" not in result.stdout:
        warn("Expected 1 failing test in test-repo but got different results")
        print(f"  {DIM}{result.stdout.splitlines()[-1] if result.stdout else '(no output)'}{RESET}")

    return TEST_REPO_PATH


# ---------------------------------------------------------------------------
# Pipeline stage display
# ---------------------------------------------------------------------------

STAGE_ICONS = {
    "passed": f"{GREEN}[PASS]{RESET}",
    "failed": f"{RED}[FAIL]{RESET}",
    "blocked": f"{YELLOW}[BLOCKED]{RESET}",
    "not_executed": f"{DIM}[ -- ]{RESET}",
}


def display_pipeline_result(run) -> None:
    """Print a formatted view of a pipeline run."""
    from src.pipeline.models import PipelineStage

    subheader(f"Pipeline Run {str(run.run_id)[:8]} - Status: {run.status.value.upper()}")

    for stage in PipelineStage:
        result = run.stage_results.get(stage)
        if result is None:
            print(f"    {STAGE_ICONS['not_executed']} {stage.value}")
            continue
        icon = STAGE_ICONS.get(result.status.value, "?")
        duration = f"{result.duration_seconds:.3f}s"
        print(f"    {icon} {stage.value:<20} ({duration})")

        if result.error:
            print(f"         {RED}Error: {result.error[:100]}{RESET}")

        # Show interesting details per stage
        if stage == PipelineStage.VALIDATION and result.output:
            for sig in result.output.get("signals", []):
                sig_icon = f"{GREEN}pass{RESET}" if sig["passed"] else f"{RED}FAIL{RESET}"
                n_findings = sig.get("findings_count", len(sig.get("findings", [])))
                print(f"         {sig['signal']}: {sig_icon} ({n_findings} findings)")
                # Show findings
                for f in sig.get("findings", [])[:5]:
                    sev = f.get("severity", "?")
                    title = f.get("title", "")
                    sev_color = RED if sev in ("error", "critical") else YELLOW if sev == "warning" else DIM
                    print(f"           {sev_color}[{sev}]{RESET} {title}")

        if stage == PipelineStage.TRUST_ROUTING and result.output:
            risk = result.output.get("risk_level", "?")
            route = result.output.get("recommended_route", "?")
            score = result.output.get("risk_score", 0)
            trust = result.output.get("agent_trust_score", 0)
            print(f"         Risk: {risk} (score={score:.2f}), Route: {route}, Agent trust: {trust:.2f}")

        if stage == PipelineStage.DEPLOY and result.output:
            action = result.output.get("action", "?")
            msg = result.output.get("message", "")
            print(f"         Action: {action}")
            print(f"         {DIM}{msg}{RESET}")

        if stage == PipelineStage.SANDBOX and result.output:
            status = result.output.get("status", "?")
            wt = result.output.get("worktree", False)
            label = "worktree" if wt else "simulated"
            print(f"         Sandbox: {status} ({label})")


# ---------------------------------------------------------------------------
# Main e2e flow
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the full end-to-end pipeline test. Returns 0 on success, 1 on failure."""
    results: list[tuple[str, bool]] = []

    header("Shipyard E2E Pipeline Test")

    # ------------------------------------------------------------------
    # Step 0: Verify test repo
    # ------------------------------------------------------------------
    subheader("Step 0: Verify test repo")
    repo_path = ensure_test_repo()
    ok(f"Test repo ready at {repo_path}")

    # ------------------------------------------------------------------
    # Step 1: Initialize runtime
    # ------------------------------------------------------------------
    subheader("Step 1: Initialize Shipyard runtime")

    # Suppress noisy logging from subsystems
    import logging
    logging.basicConfig(level=logging.WARNING)

    # Unset env vars that would trigger non-local behavior
    for var in ("OPENROUTER_API_KEY", "OPENSANDBOX_SERVER_URL", "SHIPYARD_DB_PATH", "AI_CICD_DB_PATH"):
        os.environ.pop(var, None)

    # Change to project root so relative paths in WorktreeManager resolve correctly
    os.chdir(PROJECT_ROOT)

    from src.cli.runtime import CLIRuntime
    from src.worktrees.manager import WorktreeManager

    runtime = CLIRuntime.from_defaults(storage_backend="memory")

    # Override the worktree manager to use absolute paths (the default uses
    # relative paths which break when git commands run from different cwd)
    abs_worktrees = PROJECT_ROOT / "data" / "worktrees"
    abs_repos = PROJECT_ROOT / "data" / "repos"
    runtime.worktree_manager = WorktreeManager(
        repos_dir=abs_repos,
        worktrees_dir=abs_worktrees,
    )
    # Also update the orchestrator's reference
    runtime.orchestrator._worktree_manager = runtime.worktree_manager

    ok("CLIRuntime initialized (in-memory storage)")

    # ------------------------------------------------------------------
    # Step 2: Create a project pointing at the test repo
    # ------------------------------------------------------------------
    subheader("Step 2: Create project")

    project = runtime.create_project(
        title="MathLib",
        description="A simple math library used for pipeline testing",
        priority="medium",
    )
    # Point project at the real git repo
    project.repo_url = str(repo_path.resolve())
    project.repo_local_path = str(repo_path.resolve())
    project.default_branch = "main"

    ok(f"Project created: {project.title} ({str(project.project_id)[:8]})")
    info(f"repo_url = {project.repo_url}")

    # ------------------------------------------------------------------
    # Step 3: Add milestones and goals
    # ------------------------------------------------------------------
    subheader("Step 3: Add milestones")

    pm = runtime.project_manager
    ms1 = pm.add_milestone(
        project.project_id,
        title="Bug Fixes",
        description="Fix the divide-by-zero bug in formatter.py",
        order=0,
        acceptance_criteria=[
            "format_ratio(a, 0) returns a safe string instead of raising",
            "All existing tests still pass",
            "test_format_ratio_zero_denominator passes",
        ],
    )
    ok(f"Milestone 1: {ms1.title} ({str(ms1.milestone_id)[:8]})")

    ms2 = pm.add_milestone(
        project.project_id,
        title="New Features",
        description="Add a power/exponent function to calculator.py",
        order=1,
        acceptance_criteria=[
            "power(base, exponent) function exists",
            "Tests cover basic, zero, negative, and fractional exponents",
        ],
    )
    ok(f"Milestone 2: {ms2.title} ({str(ms2.milestone_id)[:8]})")

    # ------------------------------------------------------------------
    # Step 4: Activate project (triggers cascade)
    # ------------------------------------------------------------------
    subheader("Step 4: Activate project")

    project = runtime.activate_project(str(project.project_id))
    ok(f"Project activated (status: {project.status.value})")

    # Check what goals/tasks were created
    goals = runtime.list_project_goals(str(project.project_id))
    info(f"Goals created: {len(goals)}")
    for g in goals:
        tasks = runtime.goal_manager.get_tasks(g.goal_id)
        info(f"  Goal: {g.title} ({g.status.value}) -> {len(tasks)} tasks")
        for t in tasks:
            info(f"    Task: {t.title} ({t.status.value})")

    # ------------------------------------------------------------------
    # Step 5: Agent claims and fixes the bug (Milestone 1)
    # ------------------------------------------------------------------
    header("MILESTONE 1: Bug Fixes")
    subheader("Step 5a: Agent claims implementation task")

    agent_id = "e2e-test-agent"
    runtime.trust_tracker.get_profile(agent_id)

    # Find the implementation task for the first goal
    goal1 = goals[0]
    tasks1 = runtime.goal_manager.get_tasks(goal1.goal_id)
    impl_task = None
    for t in tasks1:
        if "implement" in t.title.lower():
            impl_task = t
            break

    if impl_task is None:
        fail("Could not find implementation task")
        return 1

    # Claim the task (creates lease)
    from src.goals.models import TaskStatus
    runtime.goal_manager.update_task_status(impl_task.task_id, TaskStatus.ASSIGNED)
    lease = runtime.lease_manager.claim(impl_task.task_id, agent_id)

    # Create a worktree for the task
    wt_info = runtime.worktree_manager.create_worktree(project, impl_task)
    worktree_path = wt_info["worktree_path"]
    branch_name = wt_info["branch_name"]
    impl_task.worktree_path = worktree_path
    impl_task.branch_name = branch_name

    ok(f"Task claimed: {impl_task.title}")
    info(f"Worktree: {worktree_path}")
    info(f"Branch: {branch_name}")

    # ------------------------------------------------------------------
    # Step 5b: Agent writes the fix
    # ------------------------------------------------------------------
    subheader("Step 5b: Agent writes the fix")

    from shipyard.workspace import Workspace

    ws = Workspace(worktree_path)

    # Write the fixed formatter.py
    ws.write("src/mathlib/formatter.py", FORMATTER_FIX)
    ok("Wrote fixed formatter.py")

    # Verify tests pass in the worktree
    test_result = ws.run("python3 -m pytest tests/ -v --tb=short")
    if test_result.success:
        ok(f"All tests pass in worktree (exit code {test_result.returncode})")
    else:
        warn(f"Some tests still failing (exit {test_result.returncode})")
        print(f"  {DIM}{test_result.stdout.splitlines()[-1] if test_result.stdout else ''}{RESET}")

    # Show the diff
    diff = ws.diff()
    info(f"Diff: {len(diff)} chars")
    for line in diff.splitlines()[:15]:
        color = GREEN if line.startswith("+") else RED if line.startswith("-") else DIM
        print(f"    {color}{line}{RESET}")
    if len(diff.splitlines()) > 15:
        print(f"    {DIM}... ({len(diff.splitlines()) - 15} more lines){RESET}")

    # ------------------------------------------------------------------
    # Step 5c: Submit work (triggers pipeline)
    # ------------------------------------------------------------------
    subheader("Step 5c: Submit work -> Run pipeline")

    from src.intent.schema import IntentDeclaration

    intent = IntentDeclaration(
        agent_id=agent_id,
        description="Fix divide-by-zero bug in formatter.format_ratio",
        rationale="format_ratio(a, 0) raises ZeroDivisionError instead of returning a safe value",
        target_files=["src/mathlib/formatter.py"],
        metadata={
            "task_id": str(impl_task.task_id),
            "diff": diff,
            "worktree_path": worktree_path,
            "branch_name": branch_name,
            "test_command": "python3 -m pytest tests/ -v --tb=short",
        },
    )

    # Run the full pipeline
    pipeline_run = runtime.orchestrator.run(intent, agent_id)

    # Store worktree metadata on the run for approval/merge
    pipeline_run.metadata["worktree_path"] = worktree_path
    pipeline_run.metadata["branch_name"] = branch_name
    pipeline_run.metadata["task_id"] = str(impl_task.task_id)
    pipeline_run.metadata["description"] = intent.description
    runtime.orchestrator._save_run(pipeline_run)

    display_pipeline_result(pipeline_run)

    m1_pass = False

    if pipeline_run.status.value == "passed":
        ok("Pipeline PASSED - auto-deployed!")
        m1_pass = True

        # Commit in worktree + merge
        ws.commit("Fix divide-by-zero bug in formatter.format_ratio")
        runtime.worktree_manager.cleanup(worktree_path, str(repo_path.resolve()))
        merged = runtime.worktree_manager.merge(project, impl_task)
        if merged:
            ok("Branch merged into main")
        else:
            warn("Merge returned False (may already be merged)")

        # Mark task completed
        runtime.goal_manager.update_task_status(impl_task.task_id, TaskStatus.COMPLETED)
        runtime.lease_manager.release(impl_task.task_id, agent_id)

    elif pipeline_run.status.value == "blocked":
        warn("Pipeline BLOCKED - needs human approval")

        # Approve the run (triggers merge)
        subheader("Step 5d: Human approves the run")

        approved_run = runtime.approve_run(
            str(pipeline_run.run_id),
            comment="E2E test: approved by script",
        )
        ok(f"Run approved (status: {approved_run.status.value})")

        merged = approved_run.metadata.get("merged", False)
        if merged:
            ok("Branch merged into main on approval")
            m1_pass = True
        else:
            warn("Merge did not happen on approval - trying manual merge")
            ws.commit("Fix divide-by-zero bug in formatter.format_ratio")
            runtime.worktree_manager.cleanup(worktree_path, str(repo_path.resolve()))
            merged = runtime.worktree_manager.merge(project, impl_task)
            if merged:
                ok("Manual merge succeeded")
                m1_pass = True
            else:
                fail("Could not merge fix into main")
    else:
        fail(f"Pipeline FAILED (status: {pipeline_run.status.value})")
        # Try to clean up
        runtime.worktree_manager.cleanup(worktree_path, str(repo_path.resolve()))

    results.append(("Milestone 1: Bug fix pipeline", m1_pass))

    # ------------------------------------------------------------------
    # Step 5e: Verify fix is on main
    # ------------------------------------------------------------------
    subheader("Step 5e: Verify fix is on main")

    # Make sure we're on main
    subprocess.run(
        ["git", "checkout", "main"], cwd=repo_path,
        capture_output=True, text=True,
    )

    verify = subprocess.run(
        ["python3", "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=repo_path, capture_output=True, text=True,
    )

    if verify.returncode == 0 and "passed" in verify.stdout:
        ok("All tests pass on main after merge!")
        last_line = [l for l in verify.stdout.splitlines() if l.strip()][-1]
        info(last_line.strip())
        results.append(("Fix verified on main", True))
    else:
        if m1_pass:
            # If pipeline passed but tests fail on main, check if the merge happened
            warn(f"Tests on main: exit code {verify.returncode}")
            last_line = [l for l in verify.stdout.splitlines() if l.strip()][-1] if verify.stdout else ""
            info(last_line.strip())
        else:
            fail("Tests still failing on main")
        results.append(("Fix verified on main", verify.returncode == 0))

    # Complete the test task too (so the goal auto-completes)
    test_task = None
    for t in tasks1:
        if "test" in t.title.lower():
            test_task = t
            break
    if test_task:
        runtime.goal_manager.update_task_status(test_task.task_id, TaskStatus.COMPLETED)
        info("Test task marked completed (auto-completing goal)")

    # ------------------------------------------------------------------
    # Step 6: Milestone 2 - New feature (power function)
    # ------------------------------------------------------------------
    header("MILESTONE 2: New Features")

    # Refresh project — milestone 2 should have auto-activated
    project = runtime.show_project(str(project.project_id))
    goals2 = runtime.list_project_goals(str(project.project_id))
    goal2 = None
    for g in goals2:
        if g.goal_id != goal1.goal_id:
            goal2 = g
            break

    if goal2 is None:
        warn("Milestone 2 goal not found - may not have auto-cascaded yet")
        # The auto-cascade requires all milestone 1 goals to complete
        # Let's check status
        info(f"Project status: {project.status.value}")
        for ms in project.milestones:
            info(f"  Milestone '{ms.title}': {ms.status.value}")
        results.append(("Milestone 2: New feature pipeline", False))
    else:
        subheader("Step 6a: Agent claims power function task")

        tasks2 = runtime.goal_manager.get_tasks(goal2.goal_id)
        impl_task2 = None
        for t in tasks2:
            if "implement" in t.title.lower():
                impl_task2 = t
                break

        if impl_task2 is None:
            fail("No implementation task found for milestone 2")
            results.append(("Milestone 2: New feature pipeline", False))
        else:
            runtime.goal_manager.update_task_status(impl_task2.task_id, TaskStatus.ASSIGNED)
            lease2 = runtime.lease_manager.claim(impl_task2.task_id, agent_id)

            wt_info2 = runtime.worktree_manager.create_worktree(project, impl_task2)
            worktree_path2 = wt_info2["worktree_path"]
            branch_name2 = wt_info2["branch_name"]
            impl_task2.worktree_path = worktree_path2
            impl_task2.branch_name = branch_name2

            ok(f"Task claimed: {impl_task2.title}")
            info(f"Worktree: {worktree_path2}")

            # Write the new code
            subheader("Step 6b: Agent writes power function + tests")

            ws2 = Workspace(worktree_path2)
            ws2.write("src/mathlib/calculator.py", CALCULATOR_POWER)
            ws2.write("tests/test_power.py", POWER_TESTS)
            ok("Wrote calculator.py with power() + test_power.py")

            # Verify
            test_result2 = ws2.run("python3 -m pytest tests/ -v --tb=short")
            if test_result2.success:
                ok(f"All tests pass in worktree (exit code {test_result2.returncode})")
            else:
                warn(f"Tests: exit code {test_result2.returncode}")
                last = [l for l in test_result2.stdout.splitlines() if l.strip()]
                if last:
                    info(last[-1].strip())

            diff2 = ws2.diff()
            info(f"Diff: {len(diff2)} chars")

            # Submit
            subheader("Step 6c: Submit work -> Run pipeline")

            intent2 = IntentDeclaration(
                agent_id=agent_id,
                description="Add power/exponent function to calculator",
                rationale="Extends calculator with a power(base, exponent) function",
                target_files=["src/mathlib/calculator.py", "tests/test_power.py"],
                metadata={
                    "task_id": str(impl_task2.task_id),
                    "diff": diff2,
                    "worktree_path": worktree_path2,
                    "branch_name": branch_name2,
                    "test_command": "python3 -m pytest tests/ -v --tb=short",
                },
            )

            pipeline_run2 = runtime.orchestrator.run(intent2, agent_id)
            pipeline_run2.metadata["worktree_path"] = worktree_path2
            pipeline_run2.metadata["branch_name"] = branch_name2
            pipeline_run2.metadata["task_id"] = str(impl_task2.task_id)
            pipeline_run2.metadata["description"] = intent2.description
            runtime.orchestrator._save_run(pipeline_run2)

            display_pipeline_result(pipeline_run2)

            m2_pass = False

            if pipeline_run2.status.value == "passed":
                ok("Pipeline PASSED - auto-deployed!")
                m2_pass = True

                ws2.commit("Add power/exponent function to calculator")
                runtime.worktree_manager.cleanup(worktree_path2, str(repo_path.resolve()))
                merged2 = runtime.worktree_manager.merge(project, impl_task2)
                if merged2:
                    ok("Branch merged into main")
                else:
                    warn("Merge returned False")

                runtime.goal_manager.update_task_status(impl_task2.task_id, TaskStatus.COMPLETED)
                runtime.lease_manager.release(impl_task2.task_id, agent_id)

            elif pipeline_run2.status.value == "blocked":
                warn("Pipeline BLOCKED - needs human approval")
                subheader("Step 6d: Human approves")

                approved2 = runtime.approve_run(
                    str(pipeline_run2.run_id),
                    comment="E2E test: approved feature by script",
                )
                ok(f"Run approved (status: {approved2.status.value})")

                merged2 = approved2.metadata.get("merged", False)
                if merged2:
                    ok("Branch merged into main on approval")
                    m2_pass = True
                else:
                    warn("Trying manual merge")
                    ws2.commit("Add power/exponent function to calculator")
                    runtime.worktree_manager.cleanup(worktree_path2, str(repo_path.resolve()))
                    merged2 = runtime.worktree_manager.merge(project, impl_task2)
                    if merged2:
                        ok("Manual merge succeeded")
                        m2_pass = True
                    else:
                        fail("Could not merge feature into main")
            else:
                fail(f"Pipeline FAILED (status: {pipeline_run2.status.value})")
                runtime.worktree_manager.cleanup(worktree_path2, str(repo_path.resolve()))

            results.append(("Milestone 2: New feature pipeline", m2_pass))

            # Verify feature on main
            subheader("Step 6e: Verify feature on main")
            subprocess.run(
                ["git", "checkout", "main"], cwd=repo_path,
                capture_output=True, text=True,
            )

            verify2 = subprocess.run(
                ["python3", "-m", "pytest", "tests/", "-v", "--tb=short"],
                cwd=repo_path, capture_output=True, text=True,
            )

            if verify2.returncode == 0:
                ok("All tests pass on main (including power tests)!")
                last_line = [l for l in verify2.stdout.splitlines() if l.strip()][-1]
                info(last_line.strip())
                results.append(("Feature verified on main", True))
            else:
                warn(f"Tests on main: exit code {verify2.returncode}")
                results.append(("Feature verified on main", verify2.returncode == 0))

            # Complete test task for goal2
            test_task2 = None
            for t in tasks2:
                if "test" in t.title.lower():
                    test_task2 = t
                    break
            if test_task2:
                runtime.goal_manager.update_task_status(test_task2.task_id, TaskStatus.COMPLETED)

    # ------------------------------------------------------------------
    # Clean up lingering worktrees
    # ------------------------------------------------------------------
    subheader("Cleanup")
    wt_dir = PROJECT_ROOT / "data" / "worktrees"
    if wt_dir.exists():
        # Prune worktrees from the test repo
        subprocess.run(
            ["git", "worktree", "prune"], cwd=repo_path,
            capture_output=True, text=True,
        )
        shutil.rmtree(wt_dir, ignore_errors=True)
        ok("Cleaned up worktrees directory")
    else:
        info("No worktrees to clean up")

    # Also clean up repos dir if it was created
    repos_dir = PROJECT_ROOT / "data" / "repos"
    if repos_dir.exists():
        shutil.rmtree(repos_dir, ignore_errors=True)
        ok("Cleaned up repos directory")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    header("RESULTS SUMMARY")

    all_passed = True
    for name, passed in results:
        if passed:
            ok(name)
        else:
            fail(name)
            all_passed = False

    print()
    if all_passed:
        print(f"  {GREEN}{BOLD}ALL CHECKS PASSED{RESET}")
        print(f"  {DIM}Full pipeline tested: INTENT -> SANDBOX -> VALIDATION -> TRUST_ROUTING -> DEPLOY{RESET}")
    else:
        print(f"  {RED}{BOLD}SOME CHECKS FAILED{RESET}")
        print(f"  {DIM}Review the output above for details.{RESET}")

    print()
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
