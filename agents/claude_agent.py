"""Claude-powered Shipyard agent v4 — uses worktrees and the SDK client.

This agent:
1. Registers with the Shipyard server
2. Polls for available tasks
3. Claims a task (gets a lease + worktree if project has a repo)
4. Reads existing code from the worktree to understand context
5. Asks Claude to generate code
6. Writes files into the worktree (real files, not fake diffs)
7. Submits — server runs real pytest, ruff, bandit, behavioral diff
8. Heartbeats run automatically in the background

Usage:
    python3 agents/claude_agent4.py suricata --profile agents/profiles/backend.yaml
    python3 agents/claude_agent4.py lagarto --profile agents/profiles/qa.yaml
    python3 agents/claude_agent4.py rocky --once
"""

import argparse
import json
import os
import random
import sys
import time

# Add the SDK to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))

import anthropic
from shipyard import ShipyardClient

SHIPYARD_URL = os.environ.get("SHIPYARD_URL", "http://localhost:8001")
MODEL = os.environ.get("AGENT_MODEL", "anthropic/claude-sonnet-4-20250514")
POLL_MIN = 5
POLL_MAX = 10

# ---------------------------------------------------------------------------
# Default system prompt
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """\
You are a coding agent named {agent_name} working on a software project.
You work inside the Shipyard CI/CD pipeline as an autonomous coding agent.

Your code will be validated by real tools before it can be deployed:
- ruff (linter): max line length 100 chars, no unused imports
- bandit (security scanner): no hardcoded passwords, no shell injection
- pytest: all tests must pass

Rules:
- Write clean, production-quality Python 3.11+ code
- Use type hints on all function signatures
- Keep lines under 100 characters (enforced by ruff)
- Do NOT import modules you don't use
- Do NOT use f-strings without placeholders (use regular strings instead)
- Write focused, minimal code — only files needed for the task
- When creating a new project, include pyproject.toml with pytest config
- When writing tests, use pytest (not unittest)
- Use descriptive names, keep functions short and focused
- Handle errors properly — don't use bare except
"""

# ---------------------------------------------------------------------------
# Workspace-aware prompt
# ---------------------------------------------------------------------------

_WORKTREE_PROMPT_SUFFIX = """

WORKSPACE CONTEXT:
You are working in a real git worktree at: {worktree_path}
Branch: {branch_name}

{existing_files_section}

IMPORTANT: Your response must be ONLY valid JSON. When you create or modify files,
provide the COMPLETE file content (not just the changed parts). The system will
write these files directly to the worktree and run real tests.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(description="Claude-powered Shipyard agent v4")
    parser.add_argument("name", help="Agent name (e.g. 'suricata', 'lagarto')")
    parser.add_argument(
        "--profile", type=str, default=None,
        help="Path to agent profile YAML (e.g. agents/profiles/backend.yaml)",
    )
    parser.add_argument(
        "--capabilities", nargs="+", default=None,
        help="Agent capabilities (overrides profile)",
    )
    parser.add_argument(
        "--languages", nargs="+", default=None,
        help="Languages (overrides profile)",
    )
    parser.add_argument(
        "--frameworks", nargs="+", default=None,
        help="Frameworks (overrides profile)",
    )
    parser.add_argument(
        "--model", default=MODEL,
        help="OpenRouter model (default: %(default)s)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Process one task and exit (don't loop)",
    )
    return parser.parse_args()


def load_profile(path):
    """Load an agent profile from a YAML file."""
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)
    except ImportError:
        # Fallback: basic parsing
        profile = {}
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if ":" in stripped and not stripped.startswith(" "):
                    key, _, value = stripped.partition(":")
                    value = value.strip()
                    if value.startswith("[") and value.endswith("]"):
                        items = value[1:-1].split(",")
                        profile[key.strip()] = [
                            i.strip().strip("'\"") for i in items if i.strip()
                        ]
                    elif value == "|":
                        # Multiline — read until un-indented
                        lines = []
                        for next_line in f:
                            if next_line.startswith("  ") or next_line.startswith("\t"):
                                lines.append(next_line[2:].rstrip())
                            else:
                                break
                        profile[key.strip()] = "\n".join(lines)
                    else:
                        profile[key.strip()] = value.strip("'\"")
        return profile


def build_config(args):
    """Build agent config from args + profile."""
    config = {
        "name": args.name,
        "capabilities": ["python", "backend", "testing"],
        "languages": ["python"],
        "frameworks": ["fastapi", "pytest"],
        "system_prompt": _DEFAULT_SYSTEM_PROMPT,
    }
    if args.profile:
        profile = load_profile(args.profile)
        for key in ("capabilities", "languages", "frameworks", "system_prompt"):
            if profile.get(key):
                config[key] = profile[key]
        log(args.name, f"Loaded profile: {args.profile}")
    if args.capabilities:
        config["capabilities"] = args.capabilities
    if args.languages:
        config["languages"] = args.languages
    if args.frameworks:
        config["frameworks"] = args.frameworks
    return config


def log(agent_name, msg):
    print(f"[agent-{agent_name}] {msg}", flush=True)


def scan_workspace(workspace):
    """Read existing files from the workspace for context."""
    if workspace is None:
        return ""

    files_info = []
    try:
        all_files = workspace.list_files("**/*.py")
        # Also get yaml, toml, md
        all_files += workspace.list_files("**/*.yaml")
        all_files += workspace.list_files("**/*.yml")
        all_files += workspace.list_files("**/*.toml")
        all_files += workspace.list_files("**/*.md")

        # Limit to first 20 files to avoid overwhelming the prompt
        for filepath in sorted(set(all_files))[:20]:
            try:
                content = workspace.read(filepath)
                # Skip very large files
                if len(content) > 5000:
                    files_info.append(
                        f"### {filepath} (truncated — {len(content)} chars)\n"
                        f"{content[:2000]}\n... (truncated)"
                    )
                else:
                    files_info.append(f"### {filepath}\n{content}")
            except Exception:
                pass
    except Exception:
        pass

    if files_info:
        return "EXISTING FILES IN THE REPO:\n\n" + "\n\n".join(files_info)
    return "The repository is empty (no source files yet)."


def format_feedback_for_retry(feedback_data, local_test_output=None):
    """Format pipeline feedback and local test output into a retry prompt addition.

    Args:
        feedback_data: The FeedbackMessage dict from the pipeline.
        local_test_output: Optional string of local test stderr/stdout.

    Returns:
        A string to append to the Claude prompt for retry attempts.
    """
    parts = ["PREVIOUS ATTEMPT FAILED. Here is the error output:"]

    # Pipeline feedback
    if feedback_data:
        if isinstance(feedback_data, dict):
            msg = feedback_data.get("message", "")
            if msg:
                parts.append(f"\nPipeline result: {msg}")

            suggestions = feedback_data.get("suggestions", [])
            if suggestions:
                parts.append("\nPipeline suggestions:")
                for s in suggestions:
                    parts.append(f"  - {s}")

            validation = feedback_data.get("validation_results", {})
            if validation:
                # Extract signal results for actionable feedback
                signals = validation.get("signal_results", {})
                if signals:
                    parts.append("\nValidation signal results:")
                    for signal_name, signal_data in signals.items():
                        passed = signal_data.get("passed", "unknown")
                        detail = signal_data.get("detail", "")
                        parts.append(f"  {signal_name}: {'PASS' if passed else 'FAIL'}")
                        if detail:
                            parts.append(f"    {detail}")

                next_actions = validation.get("next_actions", [])
                if next_actions:
                    parts.append("\nRecommended actions:")
                    for action in next_actions:
                        parts.append(f"  - {action}")

    # Local test output
    if local_test_output:
        parts.append(f"\nLocal test output:\n{local_test_output[-2000:]}")

    parts.append(
        "\nFix the issues above and regenerate the files. "
        "The current files in the workspace are from your previous attempt."
    )
    return "\n".join(parts)


def ask_claude(llm_client, model, agent_name, task, system_prompt, workspace,
               feedback=None):
    """Ask Claude to generate code for a task.

    Args:
        feedback: Optional string with error/feedback from a previous attempt.
                  When present, it is appended to the user prompt so Claude
                  can see what went wrong and fix it.
    """
    formatted_system = system_prompt.format(agent_name=f"agent-{agent_name}")

    # Build context about the workspace
    if workspace:
        existing_files = scan_workspace(workspace)
        workspace_info = _WORKTREE_PROMPT_SUFFIX.format(
            worktree_path=str(workspace),
            branch_name=task.get("branch_name", "unknown"),
            existing_files_section=existing_files,
        )
    else:
        workspace_info = "\nYou are working in diff-only mode (no workspace)."

    # Fetch system-level constraints from the server
    system_constraints = ""
    try:
        import requests as _req
        resp = _req.get(f"{SHIPYARD_URL}/api/config/constraints", timeout=5)
        if resp.status_code == 200:
            cdata = resp.json()
            rules = cdata.get("constraints", [])
            if rules:
                lines = []
                for r in rules:
                    sev = r.get("severity", "SHOULD")
                    desc = r.get("description", r.get("rule", ""))
                    lines.append(f"  [{sev}] {desc}")
                system_constraints = (
                    "\n\nSYSTEM CONSTRAINTS (from the pipeline):\n"
                    + "\n".join(lines)
                )
    except Exception:
        pass

    task_constraints = task.get("constraints", [])
    constraints_text = ", ".join(task_constraints) if task_constraints else "None"

    user_prompt = f"""Task to complete:
- Title: {task.get('title', 'Unknown')}
- Description: {task.get('description', 'No description')}
- Constraints: {constraints_text}
- Target files: {', '.join(task.get('target_files', [])) or 'Agent decides'}
- Acceptance criteria: {', '.join(task.get('acceptance_criteria', [])) or 'None specified'}
{system_constraints}
{workspace_info}

Respond with ONLY valid JSON (no markdown fences, no explanation) in this format:
{{
  "files": {{
    "path/to/file.py": "complete file content here",
    "tests/test_file.py": "complete test file content"
  }},
  "description": "One paragraph explaining what you implemented and why"
}}"""

    # Append retry feedback if this is a subsequent attempt
    if feedback:
        user_prompt += f"\n\n{feedback}"

    log(agent_name, f"Asking Claude ({model})...")
    response = llm_client.messages.create(
        model=model,
        max_tokens=16384,
        system=formatted_system,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    result = json.loads(text)
    return result["files"], result["description"]


MAX_PIPELINE_ATTEMPTS = 3
MAX_LOCAL_FIX_ATTEMPTS = 2


def _write_and_prep_workspace(sdk_client, workspace, files, agent_name):
    """Write files, install deps, run ruff. Returns ruff status string."""
    sdk_client.set_phase("writing_files")
    for filepath, content in files.items():
        workspace.write(filepath, content)
        log(agent_name, f"  Wrote: {filepath}")

    # Install dependencies
    if workspace.exists("requirements.txt"):
        log(agent_name, "  Installing dependencies (requirements.txt)...")
        pip_result = workspace.run(
            "python3 -m pip install -r requirements.txt -q"
        )
        if not pip_result.success:
            log(agent_name, f"  pip install failed: {pip_result.stderr[-200:]}")
    elif workspace.exists("pyproject.toml"):
        log(agent_name, "  Installing dependencies (pyproject.toml)...")
        pip_result = workspace.run("python3 -m pip install -e . -q")
        if not pip_result.success:
            log(agent_name, "  editable install failed, trying core deps...")
            workspace.run(
                "python3 -m pip install fastapi uvicorn httpx pytest -q"
            )

    # Run ruff auto-fix before testing/submitting
    log(agent_name, "  Running ruff auto-fix...")
    workspace.run("ruff check --fix .")
    workspace.run("ruff format .")
    ruff_check = workspace.run("ruff check --output-format=text .")
    if ruff_check.returncode == 0:
        log(agent_name, "  Ruff: clean")
    else:
        remaining = len([
            line for line in ruff_check.stdout.splitlines()
            if line.strip() and not line.startswith("Found")
        ])
        log(agent_name, f"  Ruff: {remaining} issues remaining after auto-fix")


def _run_local_tests(sdk_client, workspace, agent_name):
    """Run local tests. Returns (success: bool, output: str)."""
    sdk_client.set_phase("running_tests")
    test_env = {}
    if workspace.exists("src"):
        test_env["PYTHONPATH"] = str(workspace.path / "src")
    test_result = workspace.run(
        "python3 -m pytest -p no:asyncio -x -q",
        env=test_env,
    )
    output = ""
    if test_result.success:
        log(agent_name, "  Local tests: PASSED")
    else:
        output = (test_result.stdout or "") + "\n" + (test_result.stderr or "")
        log(agent_name, f"  Local tests: FAILED (exit {test_result.returncode})")
        log(agent_name, f"  {output[-500:]}")
    return test_result.success, output


def process_task(sdk_client, llm_client, model, agent_name, task_assignment, system_prompt):
    """Process a single task with up to 3 pipeline attempts.

    Each attempt:
      1. Ask Claude for code (with feedback from prior failures if any)
      2. Write files, install deps, run ruff --fix
      3. Run local tests — if they fail, ask Claude to fix (up to 2 local retries)
      4. Submit to pipeline
      5. If accepted → done; if rejected → retry with feedback
    """
    task_id = task_assignment.task_id

    # Claim (gets lease + worktree automatically)
    try:
        claimed = sdk_client.claim_task(task_id)
        log(agent_name, f"Claimed: {claimed.title}")
        if claimed.worktree_path:
            log(agent_name, f"  Worktree: {claimed.worktree_path}")
            log(agent_name, f"  Branch: {claimed.branch_name}")
        if claimed.lease_expires_at:
            log(agent_name, f"  Lease expires: {claimed.lease_expires_at}")
    except Exception as e:
        log(agent_name, f"Claim failed: {e}")
        return

    # Build task dict for the prompt
    task_dict = {
        "title": claimed.title,
        "description": claimed.description,
        "constraints": claimed.constraints,
        "target_files": claimed.target_files,
        "acceptance_criteria": claimed.acceptance_criteria,
        "branch_name": claimed.branch_name,
    }

    workspace = sdk_client.workspace
    retry_feedback = None  # Feedback string for retry attempts

    for attempt in range(1, MAX_PIPELINE_ATTEMPTS + 1):
        log(agent_name, f"--- Attempt {attempt}/{MAX_PIPELINE_ATTEMPTS} ---")

        # Ask Claude (heartbeat runs in background automatically)
        try:
            sdk_client.set_phase("calling_llm")
            files, description = ask_claude(
                llm_client, model, agent_name, task_dict, system_prompt,
                workspace, feedback=retry_feedback,
            )
            log(
                agent_name,
                f"Claude produced {len(files)} file(s): {', '.join(files.keys())}",
            )
        except json.JSONDecodeError as e:
            log(agent_name, f"Claude returned invalid JSON: {e}")
            retry_feedback = format_feedback_for_retry(
                None, f"Claude returned invalid JSON: {e}"
            )
            continue
        except Exception as e:
            log(agent_name, f"LLM error: {e}")
            return  # Non-retryable LLM error (auth, network, etc.)

        if workspace:
            # Write files and prep workspace
            _write_and_prep_workspace(sdk_client, workspace, files, agent_name)

            # Run local tests with fix-before-submit loop
            tests_pass = False
            local_test_output = ""
            for local_attempt in range(1, MAX_LOCAL_FIX_ATTEMPTS + 1):
                tests_pass, local_test_output = _run_local_tests(
                    sdk_client, workspace, agent_name,
                )
                if tests_pass:
                    break

                if local_attempt < MAX_LOCAL_FIX_ATTEMPTS:
                    log(
                        agent_name,
                        f"  Local fix attempt {local_attempt}/{MAX_LOCAL_FIX_ATTEMPTS}"
                        " — asking Claude to fix test failures...",
                    )
                    local_fix_feedback = format_feedback_for_retry(
                        None, local_test_output,
                    )
                    try:
                        sdk_client.set_phase("calling_llm")
                        files, description = ask_claude(
                            llm_client, model, agent_name, task_dict,
                            system_prompt, workspace,
                            feedback=local_fix_feedback,
                        )
                        log(
                            agent_name,
                            f"  Claude produced {len(files)} file(s) for fix",
                        )
                        _write_and_prep_workspace(
                            sdk_client, workspace, files, agent_name,
                        )
                    except Exception as e:
                        log(agent_name, f"  LLM fix error: {e}")
                        break

            # Submit without diff — server generates it from worktree
            try:
                sdk_client.set_phase("submitting")
                feedback = sdk_client.submit_work(
                    task_id=task_id,
                    description=description,
                    files_changed=list(files.keys()),
                )
                log(agent_name, f"=> {feedback.status}: {feedback.message}")
                for s in feedback.suggestions:
                    log(agent_name, f"   -> {s}")

                if feedback.status == "accepted":
                    log(agent_name, "Task completed successfully!")
                    return
                elif feedback.status == "needs_revision":
                    log(agent_name, "Task needs human approval — moving on.")
                    return
                else:
                    # Rejected — build feedback for retry
                    feedback_dict = {
                        "message": feedback.message,
                        "suggestions": feedback.suggestions,
                        "validation_results": feedback.validation_results,
                    }
                    retry_feedback = format_feedback_for_retry(
                        feedback_dict, local_test_output,
                    )
                    if attempt < MAX_PIPELINE_ATTEMPTS:
                        log(agent_name, "Retrying with pipeline feedback...")
                    continue
            except Exception as e:
                log(agent_name, f"Submit error: {e}")
                return  # Submit errors are not retryable
        else:
            # Diff-only mode (no worktree) — no retry loop for this mode
            sdk_client.set_phase("submitting")
            diff_parts = []
            for path, content in files.items():
                lines = content.split("\n")
                diff_parts.append(f"diff --git a/{path} b/{path}")
                diff_parts.append("new file mode 100644")
                diff_parts.append(f"--- /dev/null")
                diff_parts.append(f"+++ b/{path}")
                diff_parts.append(f"@@ -0,0 +1,{len(lines)} @@")
                for fline in lines:
                    diff_parts.append(f"+{fline}")
            diff = "\n".join(diff_parts)

            try:
                feedback = sdk_client.submit_work(
                    task_id=task_id,
                    diff=diff,
                    description=description,
                    files_changed=list(files.keys()),
                )
                log(agent_name, f"=> {feedback.status}: {feedback.message}")
            except Exception as e:
                log(agent_name, f"Submit error: {e}")
            return  # No retry in diff-only mode

    # All attempts exhausted
    log(
        agent_name,
        f"All {MAX_PIPELINE_ATTEMPTS} attempts failed for task {task_id}. Moving on.",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    args = parse_args()
    agent_name = args.name
    agent_id = f"agent-{agent_name}"
    config = build_config(args)

    # Check for API key
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY not set")
        print("  export OPENROUTER_API_KEY='sk-or-v1-...'")
        sys.exit(1)

    llm_client = anthropic.Anthropic(
        api_key=api_key,
        base_url="https://openrouter.ai/api",
    )

    # Create SDK client (handles registration, heartbeats, workspace)
    sdk_client = ShipyardClient(
        base_url=SHIPYARD_URL,
        agent_id=agent_id,
        name=agent_name,
        capabilities=config["capabilities"],
        languages=config["languages"],
        frameworks=config["frameworks"],
    )

    log(agent_name, "Starting up...")
    log(agent_name, f"  Server: {SHIPYARD_URL}")
    log(agent_name, f"  Model: {args.model}")
    log(agent_name, f"  Capabilities: {config['capabilities']}")

    # Register
    try:
        sdk_client.register()
        log(agent_name, "Registered successfully")
    except Exception as e:
        log(agent_name, f"Registration warning: {e}")

    system_prompt = config["system_prompt"]

    if args.once:
        tasks = sdk_client.list_tasks()
        if tasks:
            process_task(sdk_client, llm_client, args.model, agent_name, tasks[0], system_prompt)
        else:
            log(agent_name, "No tasks available.")
        sdk_client.close()
        return

    # Loop forever
    log(agent_name, "Polling for tasks (Ctrl+C to stop)...")
    try:
        while True:
            try:
                tasks = sdk_client.list_tasks()
                if tasks:
                    task = tasks[0]
                    log(agent_name, f"Found task: '{task.title}'")
                    process_task(
                        sdk_client, llm_client, args.model,
                        agent_name, task, system_prompt,
                    )
                else:
                    log(agent_name, "No tasks. Waiting...")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log(agent_name, f"Error: {e}")

            wait = random.uniform(POLL_MIN, POLL_MAX)
            time.sleep(wait)
    except KeyboardInterrupt:
        log(agent_name, "Shutting down...")
    finally:
        sdk_client.close()


if __name__ == "__main__":
    main()
