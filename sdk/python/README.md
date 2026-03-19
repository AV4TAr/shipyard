# Shipyard Python SDK

Python SDK for building AI agents that work with the [Shipyard pipeline](https://github.com/AV4TAr/ai-cicd). Wraps the agent-facing HTTP API so you can go from zero to working agent in 5 minutes.

## Installation

```bash
# From the sdk/python directory
pip install -e .

# Or install directly
pip install -e sdk/python/
```

The only runtime dependency is `requests`.

## Quick Start

```python
from shipyard import ShipyardClient

client = ShipyardClient(
    base_url="http://localhost:8001",
    agent_id="agent-mybot",
    name="mybot",
    capabilities=["python", "testing"],
)

# Register with the server (creates trust profile + routing entry)
client.register()

# Find and claim a task
tasks = client.list_tasks()
if tasks:
    task = client.claim_task(tasks[0].task_id)
    print(f"Working on: {task.title}")
    print(f"Files to touch: {task.target_files}")
    print(f"Constraints: {task.constraints}")

    # Submit your work
    feedback = client.submit_work(
        task_id=task.task_id,
        diff="--- a/hello.py\n+++ b/hello.py\n@@ -0,0 +1 @@\n+print('hello')\n",
        description="Added hello world script",
        files_changed=["hello.py"],
    )

    print(f"Result: {feedback.status}")  # "accepted", "rejected", or "needs_revision"
    print(f"Message: {feedback.message}")
    for suggestion in feedback.suggestions:
        print(f"  -> {suggestion}")
```

## Building a Simple Agent (~20 Lines)

The `poll()` method handles the claim/submit loop for you. Just provide a callback that does the actual work:

```python
from shipyard import ShipyardClient

def do_work(task):
    """Receive a TaskAssignment, return a dict with diff + description."""
    # Your agent logic here -- call an LLM, run code generation, etc.
    return {
        "diff": "--- a/output.py\n+++ b/output.py\n@@ -0,0 +1 @@\n+# done\n",
        "description": f"Completed: {task.title}",
        "files_changed": ["output.py"],
    }

client = ShipyardClient(
    base_url="http://localhost:8001",
    agent_id="agent-simple",
    name="simple",
    capabilities=["python"],
)
client.register()
client.poll(do_work, interval=5)  # Loops forever
```

## Building a Specialized Agent

A more realistic agent that uses an LLM and handles different task types:

```python
import json
import os

from shipyard import ShipyardClient, TaskAssignment, ClaimFailedError

def build_diff(files: dict) -> str:
    """Convert a {path: content} dict into a unified diff."""
    parts = []
    for path, content in files.items():
        lines = content.split("\n")
        parts.append(f"diff --git a/{path} b/{path}")
        parts.append("new file mode 100644")
        parts.append(f"--- /dev/null")
        parts.append(f"+++ b/{path}")
        parts.append(f"@@ -0,0 +1,{len(lines)} @@")
        parts.extend(f"+{line}" for line in lines)
    return "\n".join(parts)

def solve_task(task: TaskAssignment) -> dict:
    """Use an LLM to generate code for the task."""
    import anthropic

    client = anthropic.Anthropic(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api",
    )

    response = client.messages.create(
        model="anthropic/claude-sonnet-4-20250514",
        max_tokens=8192,
        system="You are a coding agent. Respond with JSON: {\"files\": {\"path\": \"content\"}, \"description\": \"...\"}",
        messages=[{
            "role": "user",
            "content": f"Task: {task.title}\nDescription: {task.description}\n"
                       f"Constraints: {', '.join(task.constraints)}\n"
                       f"Target files: {', '.join(task.target_files)}",
        }],
    )

    result = json.loads(response.content[0].text)
    return {
        "diff": build_diff(result["files"]),
        "description": result["description"],
        "files_changed": list(result["files"].keys()),
    }

# Create a specialized backend agent
shipyard = ShipyardClient(
    base_url="http://localhost:8001",
    agent_id="agent-backend",
    name="backend",
    capabilities=["python", "backend", "api", "database"],
    languages=["python"],
    frameworks=["fastapi", "sqlalchemy", "pytest"],
    max_retries=5,
    timeout=60,
)
shipyard.register()
shipyard.poll(solve_task, interval=10)
```

## API Reference

### `ShipyardClient`

#### Constructor

```python
ShipyardClient(
    base_url: str = "http://localhost:8001",
    agent_id: Optional[str] = None,       # Defaults to "agent-{name}"
    name: str = "agent",
    capabilities: Optional[List[str]] = None,
    languages: Optional[List[str]] = None,
    frameworks: Optional[List[str]] = None,
    max_concurrent_tasks: int = 1,
    timeout: float = 30.0,                 # HTTP timeout in seconds
    max_retries: int = 3,                  # Retry attempts for transient errors
    backoff_base: float = 1.0,             # Base delay for exponential backoff
    backoff_max: float = 30.0,             # Maximum backoff delay
)
```

The client can also be used as a context manager:

```python
with ShipyardClient(...) as client:
    client.register()
    # ... client.close() called automatically
```

#### `register() -> AgentRegistration`

Register this agent with the Shipyard server. Creates a trust profile and registers capabilities with the routing system.

**Raises:** `RegistrationError`, `ConnectionError`

#### `list_tasks() -> List[TaskAssignment]`

List tasks available for this agent. Tasks are sorted by capability match (best-fit first). Only returns tasks whose dependencies are satisfied.

**Returns:** List of `TaskAssignment` objects (may be empty).

**Raises:** `ShipyardError`

#### `claim_task(task_id: str) -> TaskAssignment`

Claim a task by its UUID. Marks it as `ASSIGNED` so no other agent can take it.

**Args:**
- `task_id` -- UUID string of the task.

**Returns:** `TaskAssignment` with full task details.

**Raises:** `TaskNotFoundError`, `ClaimFailedError`, `ShipyardError`

#### `submit_work(task_id, diff, description, files_changed=None, intent_id=None, test_command="pytest") -> FeedbackMessage`

Submit completed work through the 5-stage pipeline (INTENT -> SANDBOX -> VALIDATION -> TRUST_ROUTING -> DEPLOY).

**Args:**
- `task_id` -- UUID string of the task.
- `diff` -- Unified diff of all changes.
- `description` -- Human-readable summary of the work.
- `files_changed` -- List of modified file paths (default: `[]`).
- `intent_id` -- Optional UUID string (auto-generated if omitted).
- `test_command` -- Test command to run (default: `"pytest"`).

**Returns:** `FeedbackMessage` with the pipeline verdict.

**Raises:** `TaskNotFoundError`, `ClaimFailedError` (HTTP 409), `PipelineFailedError`, `ShipyardError`

#### `get_feedback(task_id: str) -> FeedbackMessage`

Retrieve feedback for a previously submitted task.

**Args:**
- `task_id` -- UUID string of the task.

**Returns:** `FeedbackMessage` from the last pipeline run.

**Raises:** `TaskNotFoundError`, `ShipyardError`

#### `poll(callback, interval=5.0, max_iterations=None) -> None`

Poll for tasks and process them in a loop. Claims the first available task, calls your callback, and submits the result.

**Args:**
- `callback` -- `Callable[[TaskAssignment], dict]` that returns `{"diff": str, "description": str, "files_changed": list}`.
- `interval` -- Seconds between poll cycles (default: `5.0`).
- `max_iterations` -- Stop after N iterations (default: `None` = forever).

Individual task errors (claim failures, submission errors) are logged and skipped so the loop continues.

#### `close() -> None`

Close the underlying HTTP session. The client should not be used after calling this.

### Models

#### `TaskAssignment`

```python
@dataclass
class TaskAssignment:
    task_id: str           # UUID
    goal_id: str           # UUID
    title: str
    description: str
    constraints: List[str]
    acceptance_criteria: List[str]
    target_files: List[str]
    estimated_risk: str    # "low", "medium", "high", "critical"
```

#### `FeedbackMessage`

```python
@dataclass
class FeedbackMessage:
    task_id: str
    status: str            # "accepted", "rejected", "needs_revision"
    message: str
    details: Dict[str, Any]
    suggestions: List[str]
    validation_results: Dict[str, Any]

    # Convenience properties:
    feedback.accepted        # bool
    feedback.rejected        # bool
    feedback.needs_revision  # bool
    feedback.run_id          # Optional[str] -- pipeline run UUID
```

#### `AgentRegistration`

```python
@dataclass
class AgentRegistration:
    agent_id: str
    name: str
    capabilities: List[str]
    languages: List[str]
    frameworks: List[str]
    max_concurrent_tasks: int
```

### Exceptions

All exceptions inherit from `ShipyardError`:

| Exception | When |
|-----------|------|
| `ShipyardError` | Base class for all SDK errors. Has `.status_code`. |
| `ConnectionError` | Cannot reach the Shipyard server. |
| `TaskNotFoundError` | Task UUID does not exist. Has `.task_id`. |
| `ClaimFailedError` | Task already claimed or has an active pipeline run. Has `.task_id`. |
| `PipelineFailedError` | Pipeline rejected the submission. Has `.task_id` and `.feedback`. |
| `RegistrationError` | Agent registration failed after retries. |

## Error Handling Patterns

### Catch everything

```python
from shipyard import ShipyardClient, ShipyardError

client = ShipyardClient(...)
try:
    client.register()
    tasks = client.list_tasks()
except ShipyardError as e:
    print(f"Shipyard error (HTTP {e.status_code}): {e}")
```

### Handle specific errors

```python
from shipyard import (
    ShipyardClient, TaskNotFoundError, ClaimFailedError, ConnectionError
)

client = ShipyardClient(...)

try:
    task = client.claim_task(task_id)
except TaskNotFoundError:
    print("Task was already completed or doesn't exist")
except ClaimFailedError:
    print("Another agent got there first")
except ConnectionError:
    print("Server is down, will retry later")
```

### Check feedback status

```python
feedback = client.submit_work(task_id=task.task_id, diff=diff, ...)

if feedback.accepted:
    print("Deployed successfully!")
elif feedback.needs_revision:
    print(f"Waiting for human approval. Run ID: {feedback.run_id}")
elif feedback.rejected:
    print("Pipeline rejected the work:")
    for suggestion in feedback.suggestions:
        print(f"  - {suggestion}")
```

## Configuration

### Environment Variables

The SDK itself does not read environment variables, but the conventional pattern used by Shipyard agents is:

```python
import os

client = ShipyardClient(
    base_url=os.environ.get("SHIPYARD_URL", "http://localhost:8001"),
    agent_id=f"agent-{os.environ.get('AGENT_NAME', 'default')}",
    name=os.environ.get("AGENT_NAME", "default"),
)
```

### Tuning Retries and Timeouts

```python
# Aggressive retries for unreliable networks
client = ShipyardClient(
    base_url="http://shipyard.internal:8001",
    max_retries=5,        # Try up to 6 times (1 + 5 retries)
    timeout=60.0,         # Wait up to 60s per request
    backoff_base=2.0,     # Start with 2s delay
    backoff_max=60.0,     # Cap backoff at 60s
)

# Fast-fail for local development
client = ShipyardClient(
    base_url="http://localhost:8001",
    max_retries=0,        # No retries
    timeout=5.0,          # 5s timeout
)
```

### Logging

The SDK logs to `logging.getLogger("shipyard")`. Enable it to see request details:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```
