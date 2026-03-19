/**
 * TypeScript types for the Shipyard Agent SDK.
 *
 * These mirror the Pydantic models in src/sdk/protocol.py and define
 * the contract between agents and the Shipyard pipeline.
 */

// ---------------------------------------------------------------------------
// Client configuration
// ---------------------------------------------------------------------------

export interface ShipyardOptions {
  /** Base URL of the Shipyard server (e.g. "http://localhost:8001"). */
  baseUrl: string;

  /** Unique identifier for this agent (e.g. "agent-suricata"). */
  agentId: string;

  /** Human-readable agent name. */
  name: string;

  /** Capabilities the agent advertises (e.g. ["python", "backend", "testing"]). */
  capabilities: string[];

  /** Programming languages the agent can work with (e.g. ["python", "typescript"]). */
  languages?: string[];

  /** Frameworks the agent knows (e.g. ["fastapi", "pytest", "react"]). */
  frameworks?: string[];

  /** Maximum tasks this agent will work on concurrently. Default: 1. */
  maxConcurrentTasks?: number;

  /** Retry configuration. */
  retry?: RetryOptions;
}

export interface RetryOptions {
  /** Maximum number of retry attempts. Default: 5. */
  maxRetries?: number;

  /** Base delay in milliseconds for exponential backoff. Default: 1000. */
  baseDelayMs?: number;

  /** Maximum delay in milliseconds. Default: 30000. */
  maxDelayMs?: number;
}

// ---------------------------------------------------------------------------
// API models (mirrors src/sdk/protocol.py)
// ---------------------------------------------------------------------------

export interface AgentRegistration {
  agent_id: string;
  name: string;
  capabilities: string[];
  languages: string[];
  frameworks: string[];
  max_concurrent_tasks: number;
}

export interface TaskAssignment {
  task_id: string;
  goal_id: string;
  title: string;
  description: string;
  constraints: string[];
  acceptance_criteria: string[];
  target_files: string[];
  estimated_risk: "low" | "medium" | "high" | "critical";
  lease_expires_at?: string;
  lease_duration_seconds?: number;
  heartbeat_interval_seconds?: number;
  worktree_path?: string;
  branch_name?: string;
}

export interface WorkSubmission {
  task_id: string;
  agent_id: string;
  intent_id: string;
  diff?: string;
  description: string;
  test_command?: string;
  files_changed: string[];
}

export interface HeartbeatRequest {
  agent_id: string;
  phase?: string;
}

export interface HeartbeatResponse {
  task_id: string;
  lease_expires_at: string;
  lease_duration_seconds: number;
  acknowledged: boolean;
  cancel: boolean;
}

export interface FeedbackMessage {
  task_id: string;
  status: "accepted" | "rejected" | "needs_revision";
  message: string;
  details: Record<string, unknown>;
  suggestions: string[];
  validation_results: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Handler callback for the poll loop
// ---------------------------------------------------------------------------

/**
 * A function that processes a claimed task and returns a work submission
 * (minus task_id and agent_id, which the SDK fills in automatically).
 *
 * Throw an error to skip submission and release the task.
 */
export type TaskHandler = (
  task: TaskAssignment,
) => Promise<TaskHandlerResult>;

export interface TaskHandlerResult {
  /** Unified diff of all changes. */
  diff: string;

  /** Human-readable description of what was done. */
  description: string;

  /** List of file paths that were changed. */
  filesChanged: string[];

  /** Test command to run (default: "pytest"). */
  testCommand?: string;
}
