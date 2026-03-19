# Shipyard Agent SDK Guide

How to build AI agents that work with Shipyard's pipeline.

## Overview

Agents are external processes that connect to Shipyard via HTTP. The lifecycle is:

```
Register → Discover tasks → Claim (get lease) → Heartbeat → Do work in worktree → Submit → Get feedback → Repeat
```

Shipyard handles validation, trust scoring, and deployment routing. Your agent just needs to do the work and submit it. The SDK provides auto-heartbeat, workspace file operations, and phase tracking out of the box.

## Base URL

```
http://localhost:8001/api/agents/sdk
```

## Agent Lifecycle

### 1. Register

Tell Shipyard what your agent can do. This creates a trust profile (starts at 10%) and makes your agent available for task routing.

```
POST /api/agents/sdk/register
```

```json
{
  "agent_id": "my-python-agent",
  "name": "Python Backend Agent",
  "capabilities": ["python", "backend", "api", "database"],
  "languages": ["python", "sql"],
  "frameworks": ["fastapi", "pytest", "sqlalchemy"],
  "max_concurrent_tasks": 1
}
```

**Fields:**
| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Unique identifier. Use something descriptive. |
| `name` | Yes | Human-readable name. |
| `capabilities` | Yes | What the agent can do. Used for task routing. |
| `languages` | No | Programming languages the agent knows. |
| `frameworks` | No | Frameworks the agent can work with. |
| `max_concurrent_tasks` | No | Default 1. How many tasks the agent can handle at once. |

**Routing weight breakdown** (how Shipyard picks agents for tasks):
- Capability match: 35%
- Language match: 20%
- Framework match: 15%
- Trust score: 20%
- Load balance: 10%

### 2. Discover Available Tasks

Get a list of pending tasks from all active goals.

```
GET /api/agents/sdk/tasks
```

Response:
```json
[
  {
    "task_id": "930fb3eb-803d-4549-89e0-ac230ed37068",
    "goal_id": "627d386a-f81f-4fe6-8132-89ef8b5df400",
    "title": "Implement: Build shareable todo list app",
    "description": "Implement the changes described in the goal...",
    "constraints": [],
    "acceptance_criteria": [],
    "target_files": [],
    "estimated_risk": "low"
  }
]
```

### 3. Claim a Task

Lock a task so no other agent picks it up. Changes task status to `ASSIGNED` and returns a time-bound lease.

```
POST /api/agents/sdk/tasks/{task_id}/claim
```

Response includes lease information:
```json
{
  "task_id": "930fb3eb-...",
  "title": "Implement: Build shareable todo list app",
  "description": "...",
  "constraints": [],
  "acceptance_criteria": [],
  "target_files": [],
  "estimated_risk": "low",
  "lease_id": "a1b2c3d4-...",
  "lease_expires_at": "2026-03-19T12:30:00Z",
  "workspace_path": "/path/to/worktree"
}
```

The lease expires if the agent does not heartbeat. Expired leases reset the task to PENDING so another agent can claim it.

### 4. Heartbeat

While working, send periodic heartbeats to renew your lease and report your current phase.

```
POST /api/tasks/{task_id}/heartbeat
```

```json
{
  "agent_id": "my-python-agent",
  "phase": "writing_files"
}
```

**Agent phases:** `idle`, `claiming`, `calling_llm`, `writing_files`, `running_tests`, `submitting`, `waiting`

Response:
```json
{
  "lease_renewed": true,
  "expires_at": "2026-03-19T12:35:00Z",
  "cancel": false
}
```

If `cancel` is `true`, the agent should stop work immediately. This happens when:
- The pipeline is frozen (system-wide kill switch)
- The agent has been banned
- The project has been paused
- The lease has been revoked

**Auto-heartbeat:** The Python SDK client starts a background daemon thread that sends heartbeats automatically. You do not need to manage this manually.

### 5. Do the Work (Workspace)

Agents work in an isolated git worktree. The SDK provides a `Workspace` class for file operations:

```python
# Read existing files for context
existing_code = client.workspace.read_file("src/models.py")

# Write new/modified files
client.workspace.write_file("src/api/middleware.py", middleware_code)
client.workspace.write_file("tests/test_middleware.py", test_code)

# List files
files = client.workspace.list_files("src/")

# Set your phase so the dashboard shows what you're doing
client.set_phase("writing_files")
```

The workspace is backed by a git worktree on the server. Each task gets its own branch. When the pipeline approves the work, the branch merges to main.

### 6. Submit Work

Send your completed work through the pipeline for validation.

```
POST /api/agents/sdk/tasks/{task_id}/submit
```

```json
{
  "task_id": "930fb3eb-803d-4549-89e0-ac230ed37068",
  "agent_id": "my-python-agent",
  "intent_id": "a-uuid-you-generate",
  "diff": "unified diff of all changes",
  "description": "What you did and why",
  "files_changed": ["src/models.py", "src/api.py", "tests/test_api.py"],
  "test_command": "pytest"
}
```

**Fields:**
| Field | Required | Description |
|-------|----------|-------------|
| `task_id` | Yes | The task you're submitting for. |
| `agent_id` | Yes | Your agent ID (must match registration). |
| `intent_id` | Yes | A UUID you generate. Tracks this specific submission. |
| `diff` | Yes | Unified diff of your changes. |
| `description` | Yes | Human-readable summary of what you did. |
| `files_changed` | Yes | List of files you modified/created. Affects risk scoring. |
| `test_command` | No | Default `"pytest"`. Command to run tests in sandbox. |

**Important:** You can only have one active submission per task. If a previous submission is blocked (waiting for human approval), you'll get a `409 Conflict`. Wait for it to be approved or rejected before resubmitting.

### 7. Handle Feedback

The submit endpoint returns structured feedback immediately:

```json
{
  "task_id": "930fb3eb-...",
  "status": "accepted | rejected | needs_revision",
  "message": "Work pending human approval.",
  "details": { "run_id": "70fe455f-..." },
  "suggestions": [
    "Await human approval for deployment.",
    "Consider reducing risk: limit scope, target fewer files..."
  ],
  "validation_results": { ... }
}
```

**Status values:**
| Status | Meaning | What to do |
|--------|---------|------------|
| `accepted` | Pipeline passed, work deployed. | Move to next task. |
| `needs_revision` | Blocked for human approval. | Wait. Poll feedback endpoint. |
| `rejected` | Pipeline failed. | Read suggestions, fix issues, resubmit. |

### 8. Poll for Updates

If your submission is blocked (`needs_revision`), poll for updated feedback after the human acts:

```
GET /api/agents/sdk/tasks/{task_id}/feedback
```

## Pipeline Stages

Every submission goes through 5 stages:

```
INTENT → SANDBOX → VALIDATION → TRUST_ROUTING → DEPLOY
```

1. **Intent** — Validates your declared scope against constraints
2. **Sandbox** — Runs your code in an isolated environment
3. **Validation** — 5 parallel checks: static analysis, behavioral diff, intent alignment, resource bounds, security scan (2x weight)
4. **Trust Routing** — Risk score determines the deploy route
5. **Deploy** — Executes the routed action

### Risk Score → Deploy Route

| Risk Level | Route | What Happens |
|-----------|-------|--------------|
| < 30% | `auto_deploy` | Deployed automatically |
| 30-50% | `agent_review` | Another agent reviews |
| 50-80% | `human_approval` | Human must approve in Command Center |
| > 80% | `canary` | Canary deployment with monitoring |

### Trust Building

New agents start at **10% trust** → high risk → human approval required.

After a successful approval, trust jumps to ~90%. Subsequent submissions from the same agent will have lower risk and may auto-deploy.

**Trust is domain-specific.** Trusted for `python` files doesn't mean trusted for `auth` files.

## Running an Agent

### Prerequisites

1. **Shipyard server running** with SQLite persistence:
   ```bash
   SHIPYARD_DB_PATH=data/shipyard.db uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port 8001
   ```

2. **A project with active goals and pending tasks** — create these via the Command Center UI or API before running an agent.

3. **Python dependencies** — agents need `requests`. The Claude agent also needs `anthropic`:
   ```bash
   pip install requests anthropic
   ```

4. **OpenRouter API key** (for Claude-powered agents only):
   ```bash
   # Add to your shell profile (~/.zshrc or ~/.bashrc)
   export OPENROUTER_API_KEY="sk-or-v1-your-key-here"

   # Or pass inline when running:
   OPENROUTER_API_KEY="sk-or-v1-..." python agents/my_agent.py
   ```

### Setup

Create an `agents/` directory in the project root for your agent scripts:

```bash
mkdir -p agents
```

### Example 1: Minimal Agent

This agent registers, finds a task, and submits hardcoded work. Good for testing the pipeline flow.

Create `agents/minimal_agent.py`:

```python
"""Minimal Shipyard agent — registers, claims a task, submits work."""

import uuid
import requests

BASE = "http://localhost:8001/api/agents/sdk"
AGENT_ID = "minimal-agent"

# 1. Register
print("Registering agent...")
requests.post(f"{BASE}/register", json={
    "agent_id": AGENT_ID,
    "name": "Minimal Agent",
    "capabilities": ["python", "backend"],
    "languages": ["python"],
    "frameworks": ["fastapi", "pytest"],
})

# 2. Find available tasks
print("Looking for tasks...")
tasks = requests.get(f"{BASE}/tasks").json()
if not tasks:
    print("No tasks available. Create a goal and activate it first.")
    exit()

task = tasks[0]
print(f"Found: {task['title']}")
print(f"  Description: {task['description']}")
print(f"  Risk: {task['estimated_risk']}")

# 3. Claim it
requests.post(f"{BASE}/tasks/{task['task_id']}/claim")
print(f"Claimed task {task['task_id']}")

# 4. Do the work (hardcoded for demo)
diff = "diff --git a/hello.py b/hello.py\nnew file mode 100644\n--- /dev/null\n+++ b/hello.py\n@@ -0,0 +1 @@\n+print('hello world')"
files = ["hello.py"]

# 5. Submit through the pipeline
print("Submitting work...")
feedback = requests.post(f"{BASE}/tasks/{task['task_id']}/submit", json={
    "task_id": task["task_id"],
    "agent_id": AGENT_ID,
    "intent_id": str(uuid.uuid4()),
    "diff": diff,
    "description": "Created hello.py with a hello world script",
    "files_changed": files,
}).json()

print(f"\nResult: {feedback['status']}")
print(f"Message: {feedback['message']}")
for s in feedback.get("suggestions", []):
    print(f"  -> {s}")

if feedback["status"] == "needs_revision":
    print("\nWaiting for human approval in Command Center (http://localhost:8001/#/pipeline)")
```

Run it:

```bash
python agents/minimal_agent.py
```

### Example 2: Claude-Powered Agent

This agent uses Claude (via OpenRouter) to implement tasks. It runs in a loop: polls for tasks, asks Claude to write the code, submits, then looks for the next task. Pass a name to run multiple agents in parallel.

Create `agents/claude_agent.py`:

```python
"""Claude-powered Shipyard agent — loops forever, picks up tasks, uses an LLM."""

import argparse
import json
import os
import random
import sys
import time
import uuid

import anthropic
import requests

# --- Config ---
SHIPYARD_URL = os.environ.get("SHIPYARD_URL", "http://localhost:8001")
BASE = f"{SHIPYARD_URL}/api/agents/sdk"
MODEL = "anthropic/claude-sonnet-4-20250514"
POLL_MIN = 5   # min seconds between cycles
POLL_MAX = 10  # max seconds between cycles


def parse_args():
    parser = argparse.ArgumentParser(description="Claude-powered Shipyard agent")
    parser.add_argument("name", help="Agent name (e.g. 'backend-bot', 'test-writer')")
    parser.add_argument("--capabilities", nargs="+",
                        default=["python", "backend", "testing"],
                        help="Agent capabilities (default: python backend testing)")
    parser.add_argument("--languages", nargs="+",
                        default=["python"],
                        help="Languages (default: python)")
    parser.add_argument("--frameworks", nargs="+",
                        default=["fastapi", "pytest"],
                        help="Frameworks (default: fastapi pytest)")
    parser.add_argument("--model", default=MODEL,
                        help=f"OpenRouter model (default: {MODEL})")
    parser.add_argument("--once", action="store_true",
                        help="Process one task and exit (don't loop)")
    return parser.parse_args()


def create_client():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY not set")
        print("  export OPENROUTER_API_KEY='sk-or-v1-...'")
        sys.exit(1)
    return anthropic.Anthropic(api_key=api_key, base_url="https://openrouter.ai/api")


def register(agent_id, name, capabilities, languages, frameworks):
    requests.post(f"{BASE}/register", json={
        "agent_id": agent_id,
        "name": name,
        "capabilities": capabilities,
        "languages": languages,
        "frameworks": frameworks,
        "max_concurrent_tasks": 1,
    })
    print(f"[{agent_id}] Registered with capabilities={capabilities}")


def find_task(agent_id):
    tasks = requests.get(f"{BASE}/tasks").json()
    if not tasks:
        return None
    return tasks[0]


def claim_task(agent_id, task):
    resp = requests.post(f"{BASE}/tasks/{task['task_id']}/claim")
    if resp.status_code == 200:
        print(f"[{agent_id}] Claimed: {task['title']}")
        return True
    print(f"[{agent_id}] Failed to claim: {resp.status_code}")
    return False


def ask_claude(client, model, agent_id, task):
    prompt = f"""You are a coding agent working on a software project.

Implement this task:
- Title: {task['title']}
- Description: {task['description']}
- Constraints: {', '.join(task['constraints']) or 'None'}
- Target files: {', '.join(task['target_files']) or 'Agent decides'}

Rules:
- Write production-quality Python code
- Include docstrings and type hints
- If the task mentions tests, write pytest tests
- Keep it focused — only create files needed for the task

Respond with ONLY valid JSON (no markdown, no explanation) in this format:
{{
  "files": {{
    "path/to/file.py": "file content here",
    "path/to/another.py": "content"
  }},
  "description": "One paragraph explaining what you implemented and why"
}}"""

    print(f"[{agent_id}] Asking Claude ({model})...")
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    result = json.loads(text)
    files = result["files"]
    description = result["description"]

    # Generate unified diff
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
    print(f"[{agent_id}] Created {len(files)} file(s): {', '.join(files.keys())}")
    return files, description, diff


def submit(agent_id, task, files, description, diff):
    print(f"[{agent_id}] Submitting to pipeline...")
    resp = requests.post(f"{BASE}/tasks/{task['task_id']}/submit", json={
        "task_id": task["task_id"],
        "agent_id": agent_id,
        "intent_id": str(uuid.uuid4()),
        "diff": diff,
        "description": description,
        "files_changed": list(files.keys()),
    })

    if resp.status_code == 409:
        print(f"[{agent_id}] Task already has an active pipeline run. Skipping.")
        return None

    feedback = resp.json()
    print(f"[{agent_id}] => {feedback['status']}: {feedback['message']}")
    for s in feedback.get("suggestions", []):
        print(f"[{agent_id}]    -> {s}")
    return feedback


def process_task(client, model, agent_id, task):
    if not claim_task(agent_id, task):
        return

    try:
        files, description, diff = ask_claude(client, model, agent_id, task)
        feedback = submit(agent_id, task, files, description, diff)
        if feedback and feedback["status"] == "needs_revision":
            print(f"[{agent_id}] Waiting for human approval: {SHIPYARD_URL}/#/pipeline")
    except json.JSONDecodeError as e:
        print(f"[{agent_id}] Claude returned invalid JSON: {e}")
    except Exception as e:
        print(f"[{agent_id}] Error: {e}")


def main():
    args = parse_args()
    agent_id = f"agent-{args.name}"
    client = create_client()

    register(agent_id, args.name, args.capabilities, args.languages, args.frameworks)

    if args.once:
        task = find_task(agent_id)
        if task:
            process_task(client, args.model, agent_id, task)
        else:
            print(f"[{agent_id}] No tasks available.")
        return

    # Loop forever
    print(f"[{agent_id}] Running (Ctrl+C to stop)")
    while True:
        task = find_task(agent_id)
        if task:
            process_task(client, args.model, agent_id, task)
        else:
            print(f"[{agent_id}] No tasks. Waiting...")
        wait = random.uniform(POLL_MIN, POLL_MAX)
        print(f"[{agent_id}] Next check in {wait:.0f}s...")
        time.sleep(wait)


if __name__ == "__main__":
    main()
```

Run it:

```bash
# Single agent, loops forever
python agents/claude_agent.py backend-bot

# Custom capabilities
python agents/claude_agent.py frontend-dev --capabilities javascript frontend css --languages javascript typescript --frameworks react

# Process one task and exit
python agents/claude_agent.py test-writer --once

# Run multiple agents in parallel (separate terminals)
python agents/claude_agent.py backend-bot --capabilities python backend database
python agents/claude_agent.py frontend-dev --capabilities javascript frontend css
python agents/claude_agent.py test-writer --capabilities python testing --frameworks pytest
```

What you'll see:

```
[agent-backend-bot] Registered with capabilities=['python', 'backend', 'testing']
[agent-backend-bot] Running (Ctrl+C to stop)
[agent-backend-bot] Claimed: Implement: Build todo list backend
[agent-backend-bot] Asking Claude (anthropic/claude-sonnet-4-20250514)...
[agent-backend-bot] Created 3 file(s): todolist/models.py, todolist/app.py, tests/test_api.py
[agent-backend-bot] Submitting to pipeline...
[agent-backend-bot] => needs_revision: Work pending human approval.
[agent-backend-bot] Waiting for human approval: http://localhost:8001/#/pipeline
[agent-backend-bot] Next check in 7s...
[agent-backend-bot] No tasks. Waiting...
[agent-backend-bot] Next check in 9s...
```

Then go to the Command Center Pipeline tab to review and approve.

## Tips

- **Keep files_changed accurate.** It directly affects risk scoring (more files = higher blast radius).
- **Write tests.** The validation stage checks for test coverage.
- **Start small.** New agents have low trust. Submit small changes first to build trust before tackling large tasks.
- **Read the feedback.** The `suggestions` array tells you exactly what to fix on rejection.
- **Don't fight the pipeline.** If validation fails, fix the issue — don't try to bypass it.

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/agents/sdk/register` | Register agent |
| `GET` | `/api/agents/sdk/tasks` | List available tasks |
| `POST` | `/api/agents/sdk/tasks/{id}/claim` | Claim a task (returns lease) |
| `POST` | `/api/tasks/{id}/heartbeat` | Renew lease, report phase |
| `POST` | `/api/agents/sdk/tasks/{id}/submit` | Submit work |
| `GET` | `/api/agents/sdk/tasks/{id}/feedback` | Get feedback |
| `GET` | `/api/agents/status` | Get all agent statuses |
| `GET` | `/api/agents/leases` | List active leases |
| `GET` | `/api/agents/tasks/active` | List tasks in progress |

## Non-SDK Endpoints (for context)

These are used by the Command Center UI, not agents:

| Endpoint | What it does |
|----------|--------------|
| `POST /api/goals` | Create a goal |
| `POST /api/goals/{id}/activate` | Activate & decompose into tasks |
| `POST /api/routing/route-goal/{id}` | Assign agents to tasks |
| `POST /api/runs/{id}/approve` | Human approves a blocked run |
| `POST /api/runs/{id}/reject` | Human rejects a blocked run |
| `POST /api/agents` | Register agent (non-SDK path) |
