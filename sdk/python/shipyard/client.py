"""Shipyard SDK client.

Wraps the Shipyard agent-facing HTTP API so agent developers can interact
with the pipeline using simple Python method calls instead of raw HTTP.

Example::

    from shipyard import ShipyardClient

    client = ShipyardClient(
        base_url="http://localhost:8001",
        agent_id="agent-mybot",
        name="mybot",
        capabilities=["python", "testing"],
    )
    client.register()

    tasks = client.list_tasks()
    if tasks:
        task = client.claim_task(tasks[0].task_id)
        feedback = client.submit_work(
            task_id=task.task_id,
            diff="--- a/foo.py\\n+++ b/foo.py\\n...",
            description="Implemented the feature",
            files_changed=["foo.py"],
        )
        print(feedback.status)  # "accepted" / "rejected" / "needs_revision"
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import requests

from .exceptions import (
    ClaimFailedError,
    ConnectionError,
    PipelineFailedError,
    RegistrationError,
    ShipyardError,
    TaskNotFoundError,
)
from .models import AgentRegistration, FeedbackMessage, TaskAssignment

logger = logging.getLogger("shipyard")


class ShipyardClient:
    """Client for the Shipyard agent SDK API.

    Args:
        base_url: Shipyard server URL (e.g. ``"http://localhost:8001"``).
        agent_id: Unique identifier for this agent.
        name: Human-readable agent name.
        capabilities: List of capability tags (e.g. ``["python", "backend"]``).
        languages: Programming languages this agent can work with.
        frameworks: Frameworks this agent knows (e.g. ``["fastapi", "pytest"]``).
        max_concurrent_tasks: How many tasks this agent handles at once.
        timeout: HTTP request timeout in seconds.
        max_retries: Maximum retry attempts for transient failures.
        backoff_base: Base delay in seconds for exponential backoff.
        backoff_max: Maximum backoff delay in seconds.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8001",
        agent_id: Optional[str] = None,
        name: str = "agent",
        capabilities: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
        frameworks: Optional[List[str]] = None,
        max_concurrent_tasks: int = 1,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id or "agent-{}".format(name)
        self.name = name
        self.capabilities = capabilities or []
        self.languages = languages or []
        self.frameworks = frameworks or []
        self.max_concurrent_tasks = max_concurrent_tasks
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max

        self._api_base = "{}/api/agents/sdk".format(self.base_url)
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        # Heartbeat state
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = threading.Event()
        self._current_task_id: Optional[str] = None
        self._heartbeat_interval: float = 30.0
        self._current_phase: str = "idle"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        """Build a full URL from a relative SDK path."""
        return "{}{}".format(self._api_base, path)

    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        """Execute an HTTP request with retries and exponential backoff.

        Retries on connection errors and 5xx status codes. Raises
        :class:`ShipyardError` subclasses for known error patterns.
        """
        url = self._url(path)
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    timeout=self.timeout,
                )

                # Don't retry client errors (4xx) -- they won't fix themselves
                if resp.status_code < 500:
                    return resp

                # Server error -- retry
                last_error = ShipyardError(
                    "Server error: {} {}".format(resp.status_code, resp.text),
                    status_code=resp.status_code,
                )
                logger.warning(
                    "Server error %d on %s %s (attempt %d/%d)",
                    resp.status_code,
                    method,
                    path,
                    attempt + 1,
                    self.max_retries + 1,
                )

            except requests.exceptions.ConnectionError as exc:
                last_error = ConnectionError(
                    "Cannot connect to Shipyard at {}: {}".format(self.base_url, exc)
                )
                logger.warning(
                    "Connection error on %s %s (attempt %d/%d): %s",
                    method,
                    path,
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )

            except requests.exceptions.Timeout as exc:
                last_error = ConnectionError(
                    "Request timed out after {}s: {}".format(self.timeout, exc)
                )
                logger.warning(
                    "Timeout on %s %s (attempt %d/%d)",
                    method,
                    path,
                    attempt + 1,
                    self.max_retries + 1,
                )

            except requests.exceptions.RequestException as exc:
                last_error = ShipyardError("Request failed: {}".format(exc))
                logger.warning(
                    "Request error on %s %s (attempt %d/%d): %s",
                    method,
                    path,
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )

            # Exponential backoff with jitter
            if attempt < self.max_retries:
                delay = min(
                    self.backoff_base * (2 ** attempt) + random.uniform(0, 1),
                    self.backoff_max,
                )
                logger.debug("Retrying in %.1fs...", delay)
                time.sleep(delay)

        raise last_error  # type: ignore[misc]

    def _check_response(self, resp: requests.Response, task_id: Optional[str] = None) -> None:
        """Raise an appropriate exception for error responses."""
        if resp.status_code < 400:
            return

        detail = ""
        try:
            body = resp.json()
            detail = body.get("detail", str(body))
        except (ValueError, KeyError):
            detail = resp.text

        if resp.status_code == 404 and task_id:
            raise TaskNotFoundError(task_id)

        if resp.status_code == 409 and task_id:
            raise ClaimFailedError(task_id, detail)

        raise ShipyardError(
            "HTTP {} on {}: {}".format(resp.status_code, resp.url, detail),
            status_code=resp.status_code,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self) -> AgentRegistration:
        """Register this agent with the Shipyard server.

        Creates a trust profile and registers capabilities so the routing
        system can assign appropriate tasks.

        Returns:
            The confirmed :class:`AgentRegistration`.

        Raises:
            RegistrationError: If registration fails after all retries.
            ConnectionError: If the server is unreachable.
        """
        registration = AgentRegistration(
            agent_id=self.agent_id,
            name=self.name,
            capabilities=self.capabilities,
            languages=self.languages,
            frameworks=self.frameworks,
            max_concurrent_tasks=self.max_concurrent_tasks,
        )

        try:
            resp = self._request("POST", "/register", json=registration.to_dict())
        except ShipyardError:
            raise RegistrationError(
                "Failed to register agent '{}' after {} attempts".format(
                    self.agent_id, self.max_retries + 1
                )
            )

        if resp.status_code != 200:
            raise RegistrationError(
                "Registration returned HTTP {}: {}".format(resp.status_code, resp.text)
            )

        logger.info("Agent '%s' registered successfully", self.agent_id)
        return registration

    def list_tasks(self) -> List[TaskAssignment]:
        """List tasks available for this agent to claim.

        Tasks are sorted by capability match -- best-fit tasks appear first.
        Only returns tasks whose dependencies are satisfied.

        Returns:
            A list of :class:`TaskAssignment` objects (may be empty).

        Raises:
            ShipyardError: On server or connection errors.
        """
        resp = self._request("GET", "/tasks", params={"agent_id": self.agent_id})
        self._check_response(resp)

        return [TaskAssignment.from_dict(t) for t in resp.json()]

    def claim_task(self, task_id: str) -> TaskAssignment:
        """Claim a specific task for this agent.

        Marks the task as assigned and starts an automatic heartbeat
        daemon thread that keeps the lease alive until the task is
        submitted or the client is closed.

        Args:
            task_id: UUID of the task to claim.

        Returns:
            The :class:`TaskAssignment` with full details including
            lease info (``heartbeat_interval_seconds``, ``lease_expires_at``).

        Raises:
            TaskNotFoundError: If the task does not exist.
            ClaimFailedError: If the task is already claimed.
            ShipyardError: On server or connection errors.
        """
        resp = self._request(
            "POST",
            "/tasks/{}/claim".format(task_id),
            params={"agent_id": self.agent_id},
        )
        self._check_response(resp, task_id=task_id)

        task = TaskAssignment.from_dict(resp.json())
        logger.info("Claimed task '%s' (%s)", task.title, task_id)

        # Start auto-heartbeat if the server returned lease info
        data = resp.json()
        hb_interval = data.get("heartbeat_interval_seconds")
        if hb_interval:
            self._heartbeat_interval = float(hb_interval)
        self._start_heartbeat(task_id)

        # Create workspace if worktree path is available
        self._workspace = None
        if task.worktree_path:
            try:
                from .workspace import Workspace
                self._workspace = Workspace(task.worktree_path)
                logger.info("Workspace ready at %s", task.worktree_path)
            except Exception as exc:
                logger.warning("Could not create workspace: %s", exc)

        return task

    @property
    def workspace(self):
        """Return the :class:`Workspace` for the currently claimed task.

        Only available after :meth:`claim_task` on a task that has a
        ``worktree_path`` (i.e. the project has a ``repo_url``).
        Returns ``None`` when working in diff-only mode.

        Example::

            task = client.claim_task(task_id)
            if client.workspace:
                client.workspace.write("src/feature.py", code)
                result = client.workspace.run_tests("pytest tests/ -x")
        """
        return self._workspace

    def heartbeat(self, task_id: str, phase: Optional[str] = None) -> Dict[str, Any]:
        """Send a heartbeat for a claimed task to renew its lease.

        Args:
            task_id: UUID of the task.
            phase: Current agent phase (e.g. ``"calling_llm"``, ``"writing_files"``).

        Returns:
            Heartbeat response dict with lease expiry info.
        """
        payload = {"agent_id": self.agent_id}
        if phase:
            payload["phase"] = phase
        resp = self._request(
            "POST",
            "/tasks/{}/heartbeat".format(task_id),
            json=payload,
        )
        self._check_response(resp, task_id=task_id)
        return resp.json()

    def set_phase(self, phase: str) -> None:
        """Update the current agent phase (reported in heartbeats).

        Args:
            phase: One of ``"idle"``, ``"calling_llm"``, ``"writing_files"``,
                ``"running_tests"``, ``"submitting"``, ``"waiting"``.
        """
        self._current_phase = phase

    def _start_heartbeat(self, task_id: str) -> None:
        """Start the background heartbeat daemon thread."""
        self._stop_heartbeat()
        self._current_task_id = task_id
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(task_id,),
            daemon=True,
            name="shipyard-heartbeat",
        )
        self._heartbeat_thread.start()
        logger.debug(
            "Started heartbeat for task %s (interval=%.0fs)",
            task_id,
            self._heartbeat_interval,
        )

    def _stop_heartbeat(self) -> None:
        """Stop the background heartbeat daemon thread."""
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5.0)
            self._heartbeat_thread = None
        self._current_task_id = None

    def _heartbeat_loop(self, task_id: str) -> None:
        """Daemon loop that sends heartbeats at the configured interval."""
        while not self._heartbeat_stop.wait(timeout=self._heartbeat_interval):
            try:
                resp = self.heartbeat(task_id, phase=self._current_phase)
                logger.debug("Heartbeat sent for task %s", task_id)
                # Check for cancel signal from the server
                if resp.get("cancel") or not resp.get("acknowledged", True):
                    logger.warning(
                        "Heartbeat cancel signal received for task %s — stopping",
                        task_id,
                    )
                    self._heartbeat_stop.set()
                    break
            except Exception as exc:
                logger.warning("Heartbeat failed for task %s: %s", task_id, exc)

    def submit_work(
        self,
        task_id: str,
        diff: Optional[str] = None,
        description: str = "",
        files_changed: Optional[List[str]] = None,
        intent_id: Optional[str] = None,
        test_command: str = "pytest",
    ) -> FeedbackMessage:
        """Submit completed work for a task through the pipeline.

        Triggers the full 5-stage pipeline (INTENT -> SANDBOX -> VALIDATION ->
        TRUST_ROUTING -> DEPLOY) and returns structured feedback.

        Args:
            task_id: UUID of the task this work is for.
            diff: Unified diff of all changes.
            description: Human-readable summary of the work done.
            files_changed: List of file paths that were modified.
            intent_id: Optional intent UUID (auto-generated if omitted).
            test_command: Command to run tests (default: ``"pytest"``).

        Returns:
            A :class:`FeedbackMessage` with the pipeline verdict.

        Raises:
            TaskNotFoundError: If the task does not exist.
            ClaimFailedError: If there is already an active pipeline run (HTTP 409).
            PipelineFailedError: If the pipeline outright rejects the submission.
            ShipyardError: On server or connection errors.
        """
        # Stop heartbeat before submitting
        self._stop_heartbeat()
        self.set_phase("submitting")

        payload = {
            "task_id": task_id,
            "agent_id": self.agent_id,
            "intent_id": intent_id or str(uuid.uuid4()),
            "description": description,
            "test_command": test_command,
            "files_changed": files_changed or [],
        }
        if diff is not None:
            payload["diff"] = diff

        resp = self._request("POST", "/tasks/{}/submit".format(task_id), json=payload)
        self._check_response(resp, task_id=task_id)

        feedback = FeedbackMessage.from_dict(resp.json())
        logger.info(
            "Submission for task %s: %s -- %s",
            task_id,
            feedback.status,
            feedback.message,
        )
        self.set_phase("idle")
        return feedback

    def get_feedback(self, task_id: str) -> FeedbackMessage:
        """Retrieve feedback for a previously submitted task.

        Args:
            task_id: UUID of the task.

        Returns:
            The :class:`FeedbackMessage` from the last pipeline run.

        Raises:
            TaskNotFoundError: If no feedback exists for this task.
            ShipyardError: On server or connection errors.
        """
        resp = self._request("GET", "/tasks/{}/feedback".format(task_id))
        self._check_response(resp, task_id=task_id)

        return FeedbackMessage.from_dict(resp.json())

    def poll(
        self,
        callback: Callable[[TaskAssignment], Dict[str, Any]],
        interval: float = 5.0,
        max_iterations: Optional[int] = None,
    ) -> None:
        """Poll for tasks and process them in a loop.

        Convenience method that continuously:

        1. Lists available tasks
        2. Claims the first one
        3. Calls your ``callback`` with the :class:`TaskAssignment`
        4. Submits the result to the pipeline

        The ``callback`` should return a dict with::

            {
                "diff": "unified diff string",
                "description": "what was done",
                "files_changed": ["list", "of", "paths"],
            }

        Args:
            callback: Function that receives a :class:`TaskAssignment` and
                returns a dict with ``diff``, ``description``, and
                ``files_changed`` keys.
            interval: Seconds to wait between poll cycles when no tasks are
                available. Defaults to 5.
            max_iterations: Stop after this many iterations (``None`` = loop
                forever). Useful for testing.

        Raises:
            ShipyardError: Propagated from individual API calls unless handled
                internally. Claim and submission errors for individual tasks
                are logged and skipped so the loop continues.
        """
        iterations = 0
        logger.info("Starting poll loop (interval=%.1fs)", interval)

        while max_iterations is None or iterations < max_iterations:
            iterations += 1

            try:
                tasks = self.list_tasks()
            except ShipyardError as exc:
                logger.warning("Error listing tasks: %s", exc)
                time.sleep(interval)
                continue

            if not tasks:
                logger.debug("No tasks available, waiting %.1fs...", interval)
                time.sleep(interval)
                continue

            task = tasks[0]
            logger.info("Found task: '%s' (%s)", task.title, task.task_id)

            # Claim
            try:
                claimed = self.claim_task(task.task_id)
            except (ClaimFailedError, TaskNotFoundError) as exc:
                logger.warning("Could not claim task %s: %s", task.task_id, exc)
                time.sleep(interval)
                continue

            # Execute callback (heartbeat is running automatically)
            try:
                self.set_phase("calling_llm")
                result = callback(claimed)
            except Exception as exc:
                logger.error("Callback error for task %s: %s", task.task_id, exc)
                time.sleep(interval)
                continue

            # Submit
            try:
                feedback = self.submit_work(
                    task_id=claimed.task_id,
                    diff=result.get("diff", ""),
                    description=result.get("description", ""),
                    files_changed=result.get("files_changed", []),
                )
                if feedback.accepted:
                    logger.info("Task %s accepted!", claimed.task_id)
                elif feedback.needs_revision:
                    logger.info("Task %s needs human review", claimed.task_id)
                else:
                    logger.warning(
                        "Task %s rejected: %s", claimed.task_id, feedback.message
                    )
            except ShipyardError as exc:
                logger.error("Submission error for task %s: %s", task.task_id, exc)

            time.sleep(interval)

    def close(self) -> None:
        """Close the underlying HTTP session and stop heartbeats.

        Safe to call multiple times. The client should not be used after
        calling this method.
        """
        self._stop_heartbeat()
        self._session.close()

    def __enter__(self) -> "ShipyardClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return "ShipyardClient(base_url={!r}, agent_id={!r})".format(
            self.base_url, self.agent_id
        )
