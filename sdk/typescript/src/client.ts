/**
 * Shipyard SDK client — the main entry point for building TypeScript agents.
 *
 * Uses native `fetch` (Node 18+) with no external dependencies.
 */

import { randomUUID } from "node:crypto";

import { ClaimFailedError, PipelineFailedError, ShipyardError, TaskNotFoundError } from "./errors.js";
import type {
  AgentRegistration,
  FeedbackMessage,
  HeartbeatRequest,
  HeartbeatResponse,
  RetryOptions,
  ShipyardOptions,
  TaskAssignment,
  TaskHandler,
  TaskHandlerResult,
  WorkSubmission,
} from "./types.js";

const DEFAULT_RETRY: Required<RetryOptions> = {
  maxRetries: 5,
  baseDelayMs: 1000,
  maxDelayMs: 30000,
};

export class ShipyardClient {
  private readonly baseUrl: string;
  private readonly sdkBase: string;
  private readonly agentId: string;
  private readonly name: string;
  private readonly capabilities: string[];
  private readonly languages: string[];
  private readonly frameworks: string[];
  private readonly maxConcurrentTasks: number;
  private readonly retry: Required<RetryOptions>;

  private abortController: AbortController | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private heartbeatIntervalMs = 30_000;
  private currentPhase = "idle";
  private currentTaskId: string | null = null;

  constructor(options: ShipyardOptions) {
    // Strip trailing slash
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.sdkBase = `${this.baseUrl}/api/agents/sdk`;
    this.agentId = options.agentId;
    this.name = options.name;
    this.capabilities = options.capabilities;
    this.languages = options.languages ?? [];
    this.frameworks = options.frameworks ?? [];
    this.maxConcurrentTasks = options.maxConcurrentTasks ?? 1;
    this.retry = { ...DEFAULT_RETRY, ...options.retry };
  }

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------

  /**
   * Register this agent with the Shipyard server.
   */
  async register(): Promise<AgentRegistration> {
    const body: AgentRegistration = {
      agent_id: this.agentId,
      name: this.name,
      capabilities: this.capabilities,
      languages: this.languages,
      frameworks: this.frameworks,
      max_concurrent_tasks: this.maxConcurrentTasks,
    };

    return this.request<AgentRegistration>("POST", "/register", body);
  }

  /**
   * List tasks available for this agent to claim.
   */
  async listTasks(): Promise<TaskAssignment[]> {
    return this.request<TaskAssignment[]>(
      "GET",
      `/tasks?agent_id=${encodeURIComponent(this.agentId)}`,
    );
  }

  /**
   * Claim a task and start automatic heartbeat to keep the lease alive.
   *
   * @throws {TaskNotFoundError} If the task does not exist.
   * @throws {ClaimFailedError} If the task is already claimed or unavailable.
   */
  async claimTask(taskId: string): Promise<TaskAssignment> {
    try {
      const task = await this.request<TaskAssignment>(
        "POST",
        `/tasks/${encodeURIComponent(taskId)}/claim?agent_id=${encodeURIComponent(this.agentId)}`,
      );

      // Start auto-heartbeat
      if (task.heartbeat_interval_seconds) {
        this.heartbeatIntervalMs = task.heartbeat_interval_seconds * 1000;
      }
      this.startHeartbeat(taskId);

      return task;
    } catch (err) {
      if (err instanceof ShipyardError) {
        if (err.statusCode === 404) {
          throw new TaskNotFoundError(taskId, err.responseBody);
        }
        if (err.statusCode && err.statusCode >= 400) {
          throw new ClaimFailedError(taskId, err.statusCode, err.responseBody);
        }
      }
      throw err;
    }
  }

  /**
   * Send a heartbeat to renew the lease on a claimed task.
   */
  async heartbeat(taskId: string, phase?: string): Promise<HeartbeatResponse> {
    const body: HeartbeatRequest = {
      agent_id: this.agentId,
      phase: phase ?? this.currentPhase,
    };
    return this.request<HeartbeatResponse>(
      "POST",
      `/tasks/${encodeURIComponent(taskId)}/heartbeat`,
      body,
    );
  }

  /**
   * Set the current agent phase (reported in heartbeats).
   */
  setPhase(phase: string): void {
    this.currentPhase = phase;
  }

  /**
   * Submit completed work for a task and trigger the 5-stage pipeline.
   * Stops the heartbeat automatically.
   *
   * @throws {TaskNotFoundError} If the task does not exist.
   * @throws {PipelineFailedError} If the pipeline rejects the work.
   */
  async submitWork(submission: WorkSubmission): Promise<FeedbackMessage> {
    // Stop heartbeat before submitting
    this.stopHeartbeat();
    this.setPhase("submitting");

    const taskId = submission.task_id;
    let feedback: FeedbackMessage;

    try {
      feedback = await this.request<FeedbackMessage>(
        "POST",
        `/tasks/${encodeURIComponent(taskId)}/submit`,
        submission,
      );
    } catch (err) {
      if (err instanceof ShipyardError && err.statusCode === 404) {
        throw new TaskNotFoundError(taskId, err.responseBody);
      }
      throw err;
    }

    if (feedback.status === "rejected") {
      throw new PipelineFailedError(taskId, feedback);
    }

    this.setPhase("idle");
    return feedback;
  }

  /**
   * Retrieve stored feedback for a previously submitted task.
   */
  async getFeedback(taskId: string): Promise<FeedbackMessage> {
    try {
      return await this.request<FeedbackMessage>(
        "GET",
        `/tasks/${encodeURIComponent(taskId)}/feedback`,
      );
    } catch (err) {
      if (err instanceof ShipyardError && err.statusCode === 404) {
        throw new TaskNotFoundError(taskId, err.responseBody);
      }
      throw err;
    }
  }

  /**
   * Build a WorkSubmission from a TaskHandlerResult.
   */
  buildSubmission(taskId: string, result: TaskHandlerResult): WorkSubmission {
    return {
      task_id: taskId,
      agent_id: this.agentId,
      intent_id: randomUUID(),
      diff: result.diff,
      description: result.description,
      files_changed: result.filesChanged,
      test_command: result.testCommand ?? "pytest",
    };
  }

  /**
   * Poll for tasks in a loop with automatic heartbeat management.
   */
  async poll(handler: TaskHandler, intervalMs = 5000): Promise<void> {
    this.abortController = new AbortController();
    const { signal } = this.abortController;

    while (!signal.aborted) {
      try {
        const tasks = await this.listTasks();

        if (tasks.length > 0) {
          const task = tasks[0];
          try {
            const claimed = await this.claimTask(task.task_id);
            this.setPhase("calling_llm");
            const result = await handler(claimed);
            const submission = this.buildSubmission(claimed.task_id, result);
            await this.submitWork(submission);
          } catch (err) {
            this.stopHeartbeat();
            if (err instanceof ClaimFailedError) {
              continue;
            }
            console.error(`[${this.agentId}] Error processing task:`, err);
          }
        }
      } catch (err) {
        if (signal.aborted) break;
        console.error(`[${this.agentId}] Poll error:`, err);
      }

      if (!signal.aborted) {
        await this.sleep(intervalMs, signal);
      }
    }
  }

  /**
   * Stop a running poll loop and any active heartbeat.
   */
  stop(): void {
    this.stopHeartbeat();
    this.abortController?.abort();
    this.abortController = null;
  }

  // -----------------------------------------------------------------------
  // Heartbeat management
  // -----------------------------------------------------------------------

  private startHeartbeat(taskId: string): void {
    this.stopHeartbeat();
    this.currentTaskId = taskId;
    this.heartbeatTimer = setInterval(async () => {
      try {
        const resp = await this.heartbeat(taskId, this.currentPhase);
        // Check for cancel signal from the server
        if (resp.cancel || resp.acknowledged === false) {
          console.warn(`[${this.agentId}] Heartbeat cancel signal received for task ${taskId} — stopping`);
          this.stopHeartbeat();
        }
      } catch (err) {
        console.warn(`[${this.agentId}] Heartbeat failed:`, err);
      }
    }, this.heartbeatIntervalMs);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    this.currentTaskId = null;
  }

  // -----------------------------------------------------------------------
  // Internal helpers
  // -----------------------------------------------------------------------

  private async request<T>(
    method: "GET" | "POST",
    path: string,
    body?: unknown,
  ): Promise<T> {
    const url = `${this.sdkBase}${path}`;
    const { maxRetries, baseDelayMs, maxDelayMs } = this.retry;

    let lastError: Error | undefined;

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        const init: RequestInit = {
          method,
          headers: { "Content-Type": "application/json" },
        };
        if (body !== undefined) {
          init.body = JSON.stringify(body);
        }

        const resp = await fetch(url, init);

        if (resp.ok) {
          return (await resp.json()) as T;
        }

        // Parse error body
        let errorBody: unknown;
        try {
          errorBody = await resp.json();
        } catch {
          errorBody = await resp.text().catch(() => undefined);
        }

        // Don't retry client errors (4xx)
        if (resp.status >= 400 && resp.status < 500) {
          const detail =
            typeof errorBody === "object" && errorBody !== null && "detail" in errorBody
              ? (errorBody as { detail: string }).detail
              : `HTTP ${resp.status}`;
          throw new ShipyardError(detail, resp.status, errorBody);
        }

        // Server errors (5xx) — retry
        lastError = new ShipyardError(
          `Server error: HTTP ${resp.status}`,
          resp.status,
          errorBody,
        );
      } catch (err) {
        if (err instanceof ShipyardError && err.statusCode && err.statusCode < 500) {
          throw err;
        }
        lastError = err instanceof Error ? err : new Error(String(err));
      }

      if (attempt < maxRetries) {
        const delay = Math.min(
          baseDelayMs * Math.pow(2, attempt) + Math.random() * 1000,
          maxDelayMs,
        );
        await this.sleep(delay);
      }
    }

    throw lastError ?? new ShipyardError("Request failed after retries");
  }

  private sleep(ms: number, signal?: AbortSignal): Promise<void> {
    return new Promise((resolve) => {
      const timer = setTimeout(resolve, ms);
      signal?.addEventListener("abort", () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
    });
  }
}
