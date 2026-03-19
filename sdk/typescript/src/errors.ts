/**
 * Error classes for the Shipyard SDK.
 *
 * Every error extends ShipyardError so callers can catch the base class
 * for generic handling or specific subclasses for targeted recovery.
 */

export class ShipyardError extends Error {
  /** HTTP status code, if the error originated from an API response. */
  public readonly statusCode?: number;

  /** Raw response body, if available. */
  public readonly responseBody?: unknown;

  constructor(message: string, statusCode?: number, responseBody?: unknown) {
    super(message);
    this.name = "ShipyardError";
    this.statusCode = statusCode;
    this.responseBody = responseBody;
  }
}

/**
 * Thrown when the requested task does not exist (HTTP 404).
 */
export class TaskNotFoundError extends ShipyardError {
  public readonly taskId: string;

  constructor(taskId: string, responseBody?: unknown) {
    super(`Task ${taskId} not found`, 404, responseBody);
    this.name = "TaskNotFoundError";
    this.taskId = taskId;
  }
}

/**
 * Thrown when a task cannot be claimed (already assigned, race condition, etc.).
 */
export class ClaimFailedError extends ShipyardError {
  public readonly taskId: string;

  constructor(taskId: string, statusCode: number, responseBody?: unknown) {
    super(`Failed to claim task ${taskId} (HTTP ${statusCode})`, statusCode, responseBody);
    this.name = "ClaimFailedError";
    this.taskId = taskId;
  }
}

/**
 * Thrown when the pipeline rejects submitted work.
 */
export class PipelineFailedError extends ShipyardError {
  public readonly taskId: string;
  public readonly feedback: import("./types.js").FeedbackMessage;

  constructor(taskId: string, feedback: import("./types.js").FeedbackMessage) {
    super(
      `Pipeline rejected work for task ${taskId}: ${feedback.message}`,
      undefined,
      feedback,
    );
    this.name = "PipelineFailedError";
    this.taskId = taskId;
    this.feedback = feedback;
  }
}
