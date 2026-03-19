/**
 * Shipyard TypeScript SDK
 *
 * Build autonomous agents that plug into the Shipyard pipeline.
 *
 * @example
 * ```typescript
 * import { ShipyardClient } from "@shipyard/sdk";
 *
 * const client = new ShipyardClient({
 *   baseUrl: "http://localhost:8001",
 *   agentId: "agent-mybot",
 *   name: "mybot",
 *   capabilities: ["typescript", "frontend"],
 * });
 *
 * await client.register();
 * await client.poll(async (task) => {
 *   // ... do work ...
 *   return { diff, description, filesChanged };
 * });
 * ```
 */

export { ShipyardClient } from "./client.js";
export {
  ClaimFailedError,
  PipelineFailedError,
  ShipyardError,
  TaskNotFoundError,
} from "./errors.js";
export type {
  AgentRegistration,
  FeedbackMessage,
  RetryOptions,
  ShipyardOptions,
  TaskAssignment,
  TaskHandler,
  TaskHandlerResult,
  WorkSubmission,
} from "./types.js";
