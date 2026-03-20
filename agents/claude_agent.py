"""Claude-powered Shipyard agent — tool-use version.

Uses Claude's tool-use API to let the LLM iteratively write code,
run tests, see errors, and fix them — like a real developer.

Tools available to Claude:
  - write_file(path, content)  — write/overwrite a file
  - read_file(path)            — read a file
  - list_files(pattern)        — list files matching a glob
  - run_command(command)        — run a shell command (pytest, ruff, etc.)
  - task_complete(description)  — signal that the task is done

Usage:
    python3 agents/claude_agent.py suricata --profile agents/profiles/backend.yaml
    python3 agents/claude_agent.py lagarto --once
"""

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))

import anthropic
from shipyard import ShipyardClient

SHIPYARD_URL = os.environ.get("SHIPYARD_URL", "http://localhost:8001")
MODEL = os.environ.get("AGENT_MODEL", "anthropic/claude-sonnet-4-20250514")
POLL_MIN = 5
POLL_MAX = 10
MAX_TOOL_TURNS = 60  # safety limit on conversation turns


# ---------------------------------------------------------------------------
# Tool definitions (sent to Claude)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "write_file",
        "description": (
            "Write content to a file in the workspace. Creates parent "
            "directories as needed. Use this to create or update source "
            "files, test files, config files, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from the workspace root (e.g. 'app/main.py')",
                },
                "content": {
                    "type": "string",
                    "description": "The complete file content to write",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file in the workspace. Use this to "
            "understand existing code before modifying it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to read",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "List files in the workspace matching a glob pattern. "
            "Use '**/*.py' for all Python files, '*' for top-level files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. '**/*.py', '*.toml')",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command in the workspace directory. Use this to "
            "run tests (python3 -m pytest), linters (ruff check .), "
            "install deps (python3 -m pip install -r requirements.txt), etc. "
            "Returns stdout, stderr, and exit code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "task_complete",
        "description": (
            "Signal that you have finished the task. Call this ONLY when "
            "all code is written, all tests pass, and ruff reports no errors. "
            "The code will be submitted to the CI/CD pipeline for final validation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Summary of what was implemented",
                },
            },
            "required": ["description"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def setup_venv(workspace, agent_name):
    """Create a venv in the worktree and return the python path."""
    venv_dir = workspace.path / ".venv"
    if venv_dir.exists():
        log(agent_name, "  Venv already exists")
    else:
        log(agent_name, "  Creating venv...")
        result = workspace.run("python3 -m venv .venv")
        if not result.success:
            log(agent_name, f"  Venv creation failed: {result.stderr[:200]}")
            return None
    # Install base tools in venv
    venv_pip = str(venv_dir / "bin" / "pip")
    venv_python = str(venv_dir / "bin" / "python")
    workspace.run(f"{venv_pip} install --upgrade pip -q")
    workspace.run(f"{venv_pip} install ruff -q")
    log(agent_name, "  Venv ready")
    return venv_python


def execute_tool(workspace, tool_name, tool_input, venv_python=None):
    """Execute a tool call and return the result string."""
    if tool_name == "write_file":
        path = tool_input["path"]
        content = tool_input["content"]
        workspace.write(path, content)
        return f"File written: {path} ({len(content)} chars)"

    elif tool_name == "read_file":
        path = tool_input["path"]
        try:
            content = workspace.read(path)
            return content
        except FileNotFoundError:
            return f"Error: File not found: {path}"

    elif tool_name == "list_files":
        pattern = tool_input["pattern"]
        files = workspace.list_files(pattern)
        if not files:
            return "No files found matching: " + pattern
        return "\n".join(sorted(files))

    elif tool_name == "run_command":
        command = tool_input["command"]
        # Rewrite python3/pip commands to use venv if available
        if venv_python:
            venv_bin = str(Path(venv_python).parent)
            command = command.replace("python3 -m pip", f"{venv_bin}/pip")
            command = command.replace("python3 -m pytest", f"{venv_python} -m pytest")
            command = command.replace("python3 -m ", f"{venv_python} -m ")
            command = command.replace("python3 ", f"{venv_python} ")
            # ruff should use venv's ruff
            if command.startswith("ruff "):
                command = f"{venv_bin}/ruff" + command[4:]
        # Add PYTHONPATH for src/ layouts
        env = {}
        if workspace.exists("src"):
            env["PYTHONPATH"] = str(workspace.path / "src")
        result = workspace.run(command, env=env, timeout=60)
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n" + result.stderr if output else result.stderr
        status = "OK" if result.success else f"FAILED (exit {result.returncode})"
        return f"[{status}]\n{output[-3000:]}" if output else f"[{status}]"

    elif tool_name == "task_complete":
        return "TASK_COMPLETE"

    return f"Unknown tool: {tool_name}"


# ---------------------------------------------------------------------------
# System prompt and helpers
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """\
You are a coding agent named {agent_name} working inside the Shipyard CI/CD pipeline.

You have tools to write files, read files, run commands, and list files in your workspace.
Work iteratively like a real developer:

1. First, understand the task and check what already exists (list_files, read_file)
2. Create a pyproject.toml with deps and tool config
3. Write the code module by module — don't write everything at once
4. After writing each module, run tests to verify
5. Run ruff to check for lint issues, fix any problems
6. Only call task_complete when ALL tests pass and ruff is clean

CRITICAL rules:
- Use ONLY: fastapi, uvicorn, httpx, pytest, pydantic as external deps
- For databases: use Python stdlib sqlite3 — NOT sqlalchemy, NOT aiosqlite
- Keep lines under 100 characters
- No unused imports
- All tests must pass: python3 -m pytest -p no:asyncio -x
- Ruff must be clean: ruff check .
- Install deps before running tests: python3 -m pip install -r requirements.txt -q
"""

_WORKTREE_PROMPT_SUFFIX = """
WORKSPACE: {worktree_path}
BRANCH: {branch_name}

{existing_files_section}
"""


def scan_workspace(workspace):
    """Read existing files from the workspace for context."""
    if workspace is None:
        return "The workspace is not available."
    files = []
    try:
        all_files = workspace.list_files("**/*")
        py_files = [f for f in all_files if f.endswith((".py", ".toml", ".txt", ".md", ".yaml"))]
        for fp in sorted(set(py_files))[:15]:
            try:
                content = workspace.read(fp)
                if len(content) > 3000:
                    files.append(f"### {fp} (truncated)\n{content[:2000]}...")
                else:
                    files.append(f"### {fp}\n{content}")
            except Exception:
                pass
    except Exception:
        pass
    if files:
        return "EXISTING FILES:\n\n" + "\n\n".join(files)
    return "The workspace is empty — this is a new project."


def load_profile(path):
    """Load an agent profile from YAML."""
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)
    except ImportError:
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


def log(agent_name, msg):
    print(f"[agent-{agent_name}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Fetch system constraints
# ---------------------------------------------------------------------------


def fetch_constraints():
    """Fetch system constraints from the Shipyard server."""
    try:
        import requests as _req
        resp = _req.get(f"{SHIPYARD_URL}/api/config/constraints", timeout=5)
        if resp.status_code == 200:
            rules = resp.json().get("constraints", [])
            if rules:
                lines = []
                for r in rules:
                    sev = r.get("severity", "SHOULD")
                    desc = r.get("description", r.get("rule", ""))
                    lines.append(f"  [{sev}] {desc}")
                return "\nSYSTEM CONSTRAINTS:\n" + "\n".join(lines)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Tool-use conversation loop
# ---------------------------------------------------------------------------


def run_tool_use_loop(llm_client, model, agent_name, task, system_prompt,
                      workspace, venv_python=None):
    """Run the tool-use conversation loop.

    Returns (success: bool, description: str).
    """
    formatted_system = system_prompt.format(agent_name=f"agent-{agent_name}")

    # Build initial context
    existing_files = scan_workspace(workspace)
    constraints = fetch_constraints()

    workspace_info = ""
    if workspace:
        workspace_info = _WORKTREE_PROMPT_SUFFIX.format(
            worktree_path=str(workspace),
            branch_name=task.get("branch_name", "unknown"),
            existing_files_section=existing_files,
        )

    user_message = f"""Task to complete:
- Title: {task.get('title', 'Unknown')}
- Description: {task.get('description', 'No description')}
- Constraints: {', '.join(task.get('constraints', [])) or 'None'}
- Acceptance criteria: {', '.join(task.get('acceptance_criteria', [])) or 'None'}
{constraints}
{workspace_info}

Work iteratively: write code, run tests, fix issues, repeat until everything passes.
Then call task_complete."""

    messages = [{"role": "user", "content": user_message}]

    for turn in range(MAX_TOOL_TURNS):
        log(agent_name, f"  Turn {turn + 1}/{MAX_TOOL_TURNS}")

        response = llm_client.messages.create(
            model=model,
            max_tokens=16384,
            system=formatted_system,
            tools=TOOLS,
            messages=messages,
        )

        # Process the response
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # Check if Claude wants to use tools
        tool_uses = [b for b in assistant_content if b.type == "tool_use"]

        if not tool_uses:
            # Claude sent text only — it might be thinking or done
            text = "".join(
                b.text for b in assistant_content if hasattr(b, "text")
            )
            if text:
                log(agent_name, f"  Claude: {text[:200]}")
            break

        # Execute each tool call
        tool_results = []
        task_done = False
        done_description = ""

        for tool_use in tool_uses:
            tool_name = tool_use.name
            tool_input = tool_use.input

            if tool_name == "write_file":
                log(agent_name, f"  -> write_file: {tool_input['path']}")
            elif tool_name == "run_command":
                log(agent_name, f"  -> run: {tool_input['command'][:60]}")
            elif tool_name == "read_file":
                log(agent_name, f"  -> read: {tool_input['path']}")
            elif tool_name == "list_files":
                log(agent_name, f"  -> list: {tool_input['pattern']}")
            elif tool_name == "task_complete":
                log(agent_name, f"  -> TASK COMPLETE: {tool_input['description'][:80]}")

            result = execute_tool(workspace, tool_name, tool_input, venv_python)

            if result == "TASK_COMPLETE":
                task_done = True
                done_description = tool_input["description"]
                result = "Task marked as complete. Submitting to pipeline."

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result[:4000],  # truncate large outputs
            })

        messages.append({"role": "user", "content": tool_results})

        if task_done:
            return True, done_description

    log(agent_name, f"  Reached max turns ({MAX_TOOL_TURNS})")
    return False, "Max turns reached without completing"


# ---------------------------------------------------------------------------
# Process task
# ---------------------------------------------------------------------------


def process_task(sdk_client, llm_client, model, agent_name, task_assignment,
                 system_prompt):
    """Claim a task and run the tool-use loop."""
    task_id = task_assignment.task_id

    try:
        claimed = sdk_client.claim_task(task_id)
        log(agent_name, f"Claimed: {claimed.title}")
        if claimed.worktree_path:
            log(agent_name, f"  Worktree: {claimed.worktree_path}")
            log(agent_name, f"  Branch: {claimed.branch_name}")
        if claimed.lease_expires_at:
            log(agent_name, f"  Lease: {claimed.lease_expires_at}")
    except Exception as e:
        log(agent_name, f"Claim failed: {e}")
        return

    workspace = sdk_client.workspace
    if not workspace:
        log(agent_name, "No workspace — skipping (worktree not created)")
        return

    task_dict = {
        "title": claimed.title,
        "description": claimed.description,
        "constraints": claimed.constraints,
        "target_files": claimed.target_files,
        "acceptance_criteria": claimed.acceptance_criteria,
        "branch_name": claimed.branch_name,
    }

    # Set up venv in the worktree
    venv_python = setup_venv(workspace, agent_name)

    # Run the tool-use conversation
    sdk_client.set_phase("calling_llm")
    try:
        success, description = run_tool_use_loop(
            llm_client, model, agent_name, task_dict, system_prompt,
            workspace, venv_python=venv_python,
        )
    except Exception as e:
        log(agent_name, f"Tool-use loop error: {e}")
        return

    if not success:
        log(agent_name, "Agent could not complete the task")
        return

    # Submit to pipeline
    sdk_client.set_phase("submitting")
    try:
        files_changed = workspace.changed_files()
        feedback = sdk_client.submit_work(
            task_id=task_id,
            description=description,
            files_changed=files_changed,
        )
        log(agent_name, f"=> {feedback.status}: {feedback.message}")
        for s in feedback.suggestions:
            log(agent_name, f"   -> {s}")
    except Exception as e:
        log(agent_name, f"Submit error: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(description="Claude Shipyard agent (tool-use)")
    parser.add_argument("name", help="Agent name")
    parser.add_argument("--profile", type=str, default=None)
    parser.add_argument("--capabilities", nargs="+", default=None)
    parser.add_argument("--languages", nargs="+", default=None)
    parser.add_argument("--frameworks", nargs="+", default=None)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def build_config(args):
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


def main():
    args = parse_args()
    agent_name = args.name
    config = build_config(args)

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY not set")
        sys.exit(1)

    llm_client = anthropic.Anthropic(
        api_key=api_key,
        base_url="https://openrouter.ai/api",
    )

    sdk_client = ShipyardClient(
        base_url=SHIPYARD_URL,
        agent_id=f"agent-{agent_name}",
        name=agent_name,
        capabilities=config["capabilities"],
        languages=config["languages"],
        frameworks=config["frameworks"],
    )

    log(agent_name, "Starting up (tool-use mode)...")
    log(agent_name, f"  Server: {SHIPYARD_URL}")
    log(agent_name, f"  Model: {args.model}")

    try:
        sdk_client.register()
        log(agent_name, "Registered")
    except Exception as e:
        log(agent_name, f"Registration warning: {e}")

    system_prompt = config["system_prompt"]

    if args.once:
        tasks = sdk_client.list_tasks()
        if tasks:
            process_task(
                sdk_client, llm_client, args.model, agent_name,
                tasks[0], system_prompt,
            )
        else:
            log(agent_name, "No tasks available.")
        sdk_client.close()
        return

    log(agent_name, "Polling (Ctrl+C to stop)...")
    try:
        while True:
            try:
                tasks = sdk_client.list_tasks()
                if tasks:
                    log(agent_name, f"Found: '{tasks[0].title}'")
                    process_task(
                        sdk_client, llm_client, args.model, agent_name,
                        tasks[0], system_prompt,
                    )
                else:
                    log(agent_name, "No tasks. Waiting...")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log(agent_name, f"Error: {e}")
            time.sleep(random.uniform(POLL_MIN, POLL_MAX))
    except KeyboardInterrupt:
        log(agent_name, "Shutting down...")
    finally:
        sdk_client.close()


if __name__ == "__main__":
    main()
