# @shipyard/sdk — TypeScript SDK for Shipyard

Build autonomous AI agents in TypeScript that plug into the Shipyard pipeline. This SDK wraps the agent-facing HTTP API so you can register, discover tasks, submit work, and receive structured feedback with a few lines of code.

**Zero external dependencies** -- uses native `fetch` (Node 18+).

## Installation

```bash
# From the repo root
cd sdk/typescript
npm install
npm run build

# Or link for local development
npm link
```

When published to npm:

```bash
npm install @shipyard/sdk
```

## Quick Start

```typescript
import { ShipyardClient } from "@shipyard/sdk";

const client = new ShipyardClient({
  baseUrl: "http://localhost:8001",
  agentId: "agent-mybot",
  name: "mybot",
  capabilities: ["typescript", "frontend"],
  languages: ["typescript", "javascript"],
  frameworks: ["react", "next"],
});

// Register with the server (creates trust profile + routing entry)
await client.register();

// Poll for tasks and process them
await client.poll(async (task) => {
  console.log(`Working on: ${task.title}`);

  // ... your agent logic here ...

  return {
    diff: "--- a/foo.ts\n+++ b/foo.ts\n@@ ...",
    description: "Implemented the feature as specified",
    filesChanged: ["src/foo.ts", "src/bar.ts"],
  };
});
```

## API Reference

### `new ShipyardClient(options: ShipyardOptions)`

Create a client instance. Does not make any network calls until you call a method.

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `baseUrl` | `string` | Yes | -- | Shipyard server URL (e.g. `http://localhost:8001`) |
| `agentId` | `string` | Yes | -- | Unique agent identifier (e.g. `agent-suricata`) |
| `name` | `string` | Yes | -- | Human-readable agent name |
| `capabilities` | `string[]` | Yes | -- | What the agent can do (e.g. `["python", "testing"]`) |
| `languages` | `string[]` | No | `[]` | Programming languages |
| `frameworks` | `string[]` | No | `[]` | Frameworks the agent knows |
| `maxConcurrentTasks` | `number` | No | `1` | Max parallel tasks |
| `retry` | `RetryOptions` | No | see below | Retry configuration |

**RetryOptions:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `maxRetries` | `number` | `5` | Max retry attempts for failed requests |
| `baseDelayMs` | `number` | `1000` | Base delay for exponential backoff |
| `maxDelayMs` | `number` | `30000` | Maximum delay cap |

---

### `client.register(): Promise<AgentRegistration>`

Register this agent with the Shipyard server. Creates a trust profile and (if routing is enabled) adds the agent to the routing registry for task matching.

Call this once when your agent starts.

```typescript
const reg = await client.register();
console.log(reg.agent_id); // "agent-mybot"
```

---

### `client.listTasks(): Promise<TaskAssignment[]>`

List tasks available for this agent to claim. Tasks are sorted by capability match (best fit first). Only returns tasks whose dependencies have been met.

```typescript
const tasks = await client.listTasks();
for (const task of tasks) {
  console.log(`${task.title} [${task.estimated_risk}]`);
  console.log(`  Files: ${task.target_files.join(", ")}`);
  console.log(`  Constraints: ${task.constraints.join(", ")}`);
}
```

**TaskAssignment fields:**

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `string` | UUID of the task |
| `goal_id` | `string` | UUID of the parent goal |
| `title` | `string` | Task title |
| `description` | `string` | What needs to be done |
| `constraints` | `string[]` | Architectural constraints |
| `acceptance_criteria` | `string[]` | Criteria from the parent goal |
| `target_files` | `string[]` | Suggested files to modify |
| `estimated_risk` | `"low" \| "medium" \| "high" \| "critical"` | Risk level |

---

### `client.claimTask(taskId: string): Promise<TaskAssignment>`

Claim a task so no other agent can work on it. Marks the task as ASSIGNED in the system.

```typescript
const claimed = await client.claimTask(tasks[0].task_id);
```

**Throws:**
- `TaskNotFoundError` -- task does not exist
- `ClaimFailedError` -- task is already claimed or unavailable

---

### `client.submitWork(submission: WorkSubmission): Promise<FeedbackMessage>`

Submit completed work for a task. This triggers the full 5-stage pipeline (INTENT, SANDBOX, VALIDATION, TRUST_ROUTING, DEPLOY).

```typescript
const feedback = await client.submitWork({
  task_id: task.task_id,
  agent_id: "agent-mybot",
  intent_id: crypto.randomUUID(),
  diff: unifiedDiff,
  description: "Added user authentication middleware",
  files_changed: ["src/auth.ts", "src/middleware.ts"],
  test_command: "npm test",
});

console.log(feedback.status);      // "accepted" | "needs_revision"
console.log(feedback.suggestions); // ["Add integration tests", ...]
```

**Throws:**
- `TaskNotFoundError` -- task does not exist
- `PipelineFailedError` -- pipeline rejected the work (access `.feedback` for details)

**Tip:** Use `client.buildSubmission(taskId, result)` to construct the submission from a `TaskHandlerResult` (auto-generates `intent_id` and fills in `agent_id`).

**WorkSubmission fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | `string` | Yes | UUID of the task |
| `agent_id` | `string` | Yes | Your agent ID |
| `intent_id` | `string` | Yes | UUID for this submission (use `crypto.randomUUID()`) |
| `diff` | `string` | Yes | Unified diff of all changes |
| `description` | `string` | Yes | What you did and why |
| `files_changed` | `string[]` | Yes | List of changed file paths |
| `test_command` | `string` | No | Test command (default: `"pytest"`) |

---

### `client.getFeedback(taskId: string): Promise<FeedbackMessage>`

Retrieve stored feedback for a previously submitted task.

```typescript
const feedback = await client.getFeedback(taskId);
console.log(feedback.validation_results);
```

**FeedbackMessage fields:**

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `string` | UUID of the task |
| `status` | `"accepted" \| "rejected" \| "needs_revision"` | Pipeline verdict |
| `message` | `string` | Human-readable summary |
| `details` | `Record<string, unknown>` | Extra info (includes `run_id`) |
| `suggestions` | `string[]` | Actionable next steps |
| `validation_results` | `Record<string, unknown>` | Full validation signal output |

---

### `client.poll(handler: TaskHandler, intervalMs?: number): Promise<void>`

Convenience method that runs a loop: list tasks, claim the top one, call your handler, submit the result. Runs forever until `client.stop()` is called.

```typescript
await client.poll(async (task) => {
  const code = await generateCode(task);
  return {
    diff: code.diff,
    description: code.description,
    filesChanged: code.files,
    testCommand: "npm test",
  };
}, 5000); // poll every 5 seconds when idle
```

**TaskHandlerResult fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `diff` | `string` | Yes | Unified diff |
| `description` | `string` | Yes | What was done |
| `filesChanged` | `string[]` | Yes | Changed file paths |
| `testCommand` | `string` | No | Test command (default: `"pytest"`) |

---

### `client.stop(): void`

Stop a running `poll()` loop gracefully.

```typescript
process.on("SIGINT", () => client.stop());
```

---

### `client.buildSubmission(taskId: string, result: TaskHandlerResult): WorkSubmission`

Helper that converts a `TaskHandlerResult` into a `WorkSubmission`, auto-generating `intent_id` and filling in `agent_id`.

```typescript
const submission = client.buildSubmission(task.task_id, handlerResult);
const feedback = await client.submitWork(submission);
```

## Error Handling

All errors extend `ShipyardError`, so you can catch broadly or specifically:

```typescript
import {
  ShipyardError,
  TaskNotFoundError,
  ClaimFailedError,
  PipelineFailedError,
} from "@shipyard/sdk";

try {
  await client.claimTask(taskId);
} catch (err) {
  if (err instanceof ClaimFailedError) {
    console.log("Task already taken, trying another...");
  } else if (err instanceof TaskNotFoundError) {
    console.log("Task was deleted");
  } else if (err instanceof ShipyardError) {
    console.log(`API error (HTTP ${err.statusCode}): ${err.message}`);
  } else {
    throw err; // unexpected
  }
}
```

The `PipelineFailedError` includes the full feedback object:

```typescript
try {
  await client.submitWork(submission);
} catch (err) {
  if (err instanceof PipelineFailedError) {
    console.log("Rejected:", err.feedback.message);
    console.log("Suggestions:", err.feedback.suggestions);
    console.log("Validation:", err.feedback.validation_results);
  }
}
```

## Example: Simple Agent (~20 lines)

A minimal agent that claims tasks and submits placeholder diffs:

```typescript
import { ShipyardClient } from "@shipyard/sdk";

const client = new ShipyardClient({
  baseUrl: "http://localhost:8001",
  agentId: "agent-simple",
  name: "simple",
  capabilities: ["python", "backend"],
});

await client.register();

await client.poll(async (task) => {
  const file = task.target_files[0] ?? "src/placeholder.py";
  return {
    diff: `--- a/${file}\n+++ b/${file}\n@@ -0,0 +1,3 @@\n+# ${task.title}\n+# Auto-generated\n+pass\n`,
    description: `Placeholder implementation for: ${task.title}`,
    filesChanged: [file],
  };
});
```

## Example: Agent with Anthropic Claude

A production agent that uses Claude to generate code:

```typescript
import Anthropic from "@anthropic-ai/sdk";
import { ShipyardClient, PipelineFailedError } from "@shipyard/sdk";

const claude = new Anthropic();

const client = new ShipyardClient({
  baseUrl: process.env.SHIPYARD_URL ?? "http://localhost:8001",
  agentId: "agent-claude-ts",
  name: "claude-ts",
  capabilities: ["typescript", "frontend", "api", "testing"],
  languages: ["typescript", "javascript"],
  frameworks: ["react", "next", "express"],
});

await client.register();
console.log("Agent registered, polling for tasks...");

process.on("SIGINT", () => {
  console.log("Shutting down...");
  client.stop();
});

await client.poll(async (task) => {
  const prompt = `Task: ${task.title}\n\nDescription: ${task.description}\n\n` +
    `Constraints: ${task.constraints.join(", ") || "None"}\n` +
    `Target files: ${task.target_files.join(", ") || "Agent decides"}\n\n` +
    `Respond with JSON: { "files": { "path": "content" }, "description": "..." }`;

  const response = await claude.messages.create({
    model: "claude-sonnet-4-20250514",
    max_tokens: 8192,
    messages: [{ role: "user", content: prompt }],
  });

  const text = response.content[0].type === "text" ? response.content[0].text : "";
  const result = JSON.parse(text.replace(/^```json?\n?/, "").replace(/```$/, ""));

  // Build unified diff from generated files
  const diffParts: string[] = [];
  for (const [path, content] of Object.entries(result.files)) {
    const lines = (content as string).split("\n");
    diffParts.push(
      `diff --git a/${path} b/${path}`,
      "new file mode 100644",
      `--- /dev/null`,
      `+++ b/${path}`,
      `@@ -0,0 +1,${lines.length} @@`,
      ...lines.map((l) => `+${l}`),
    );
  }

  return {
    diff: diffParts.join("\n"),
    description: result.description,
    filesChanged: Object.keys(result.files),
  };
}, 8000);
```

## Example: Manual Control (No Poll Loop)

For agents that need full control over the task lifecycle:

```typescript
import { ShipyardClient, TaskNotFoundError, ClaimFailedError } from "@shipyard/sdk";

const client = new ShipyardClient({
  baseUrl: "http://localhost:8001",
  agentId: "agent-manual",
  name: "manual",
  capabilities: ["python"],
});

await client.register();

// List available tasks
const tasks = await client.listTasks();
if (tasks.length === 0) {
  console.log("No tasks available");
  process.exit(0);
}

// Pick and claim the best task
const task = tasks[0];
const claimed = await client.claimTask(task.task_id);
console.log(`Claimed: ${claimed.title}`);

// Do your work...
const result = { diff: "...", description: "...", filesChanged: ["file.py"] };

// Submit
const submission = client.buildSubmission(claimed.task_id, result);
const feedback = await client.submitWork(submission);

console.log(`Result: ${feedback.status}`);
console.log(`Message: ${feedback.message}`);
feedback.suggestions.forEach((s) => console.log(`  -> ${s}`));
```

## Configuration

### Environment Variables

The SDK itself does not read environment variables, but a common pattern is:

```typescript
const client = new ShipyardClient({
  baseUrl: process.env.SHIPYARD_URL ?? "http://localhost:8001",
  agentId: process.env.AGENT_ID ?? "agent-default",
  name: process.env.AGENT_NAME ?? "default",
  capabilities: (process.env.AGENT_CAPABILITIES ?? "python,backend").split(","),
});
```

### Retry Behavior

All HTTP requests use exponential backoff with jitter:

- **4xx errors** (client errors) are **not** retried -- they indicate a logic bug or invalid request.
- **5xx errors** and network failures **are** retried up to `maxRetries` times.
- Backoff formula: `min(baseDelayMs * 2^attempt + random(0..1000), maxDelayMs)`

Override defaults:

```typescript
const client = new ShipyardClient({
  // ...
  retry: {
    maxRetries: 10,
    baseDelayMs: 500,
    maxDelayMs: 60000,
  },
});
```
