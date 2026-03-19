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


def ask_claude(llm_client, model, agent_name, task, system_prompt, workspace):
    """Ask Claude to generate code for a task."""
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

    user_prompt = f"""Task to complete:
- Title: {task.get('title', 'Unknown')}
- Description: {task.get('description', 'No description')}
- Constraints: {', '.join(task.get('constraints', [])) or 'None'}
- Target files: {', '.join(task.get('target_files', [])) or 'Agent decides'}
- Acceptance criteria: {', '.join(task.get('acceptance_criteria', [])) or 'None specified'}
{workspace_info}

Respond with ONLY valid JSON (no markdown fences, no explanation) in this format:
{{
  "files": {{
    "path/to/file.py": "complete file content here",
    "tests/test_file.py": "complete test file content"
  }},
  "description": "One paragraph explaining what you implemented and why"
}}"""

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


def process_task(sdk_client, llm_client, model, agent_name, task_assignment, system_prompt):
    """Process a single task: claim → write code → submit."""
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

    # Ask Claude (heartbeat runs in background automatically)
    try:
        sdk_client.set_phase("calling_llm")
        files, description = ask_claude(
            llm_client, model, agent_name, task_dict, system_prompt, workspace,
        )
        log(agent_name, f"Claude produced {len(files)} file(s): {', '.join(files.keys())}")
    except json.JSONDecodeError as e:
        log(agent_name, f"Claude returned invalid JSON: {e}")
        return
    except Exception as e:
        log(agent_name, f"LLM error: {e}")
        return

    # Write files to worktree (or build fake diff)
    if workspace:
        sdk_client.set_phase("writing_files")
        for filepath, content in files.items():
            workspace.write(filepath, content)
            log(agent_name, f"  Wrote: {filepath}")

        # Run tests locally first to give quick feedback
        sdk_client.set_phase("running_tests")
        test_result = workspace.run_tests("python3 -m pytest -x -q")
        if test_result.success:
            log(agent_name, "  Local tests: PASSED")
        else:
            log(agent_name, f"  Local tests: FAILED (exit {test_result.returncode})")
            log(agent_name, f"  {test_result.stdout[-500:]}" if test_result.stdout else "")

        # Submit without diff — server generates it from worktree
        try:
            feedback = sdk_client.submit_work(
                task_id=task_id,
                description=description,
                files_changed=list(files.keys()),
            )
            log(agent_name, f"=> {feedback.status}: {feedback.message}")
            for s in feedback.suggestions:
                log(agent_name, f"   -> {s}")
        except Exception as e:
            log(agent_name, f"Submit error: {e}")
    else:
        # Diff-only mode (no worktree)
        sdk_client.set_phase("submitting")
        diff_parts = []
        for path, content in files.items():
            lines = content.split("\n")
            diff_parts.append(f"diff --git a/{path} b/{path}")
            diff_parts.append("new file mode 100644")
            diff_parts.append(f"--- /dev/null")
            diff_parts.append(f"+++ b/{path}")
            diff_parts.append(f"@@ -0,0 +1,{len(lines)} @@")
            for line in lines:
                diff_parts.append(f"+{line}")
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
