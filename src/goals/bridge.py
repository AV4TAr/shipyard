"""Bridge between the Goals system and the existing Pipeline Orchestrator.

Converts AgentTasks into IntentDeclarations so they can flow through the
full CI/CD pipeline (intent validation, sandbox, validation gate, trust
routing, deploy).
"""

from __future__ import annotations

from src.intent.schema import IntentDeclaration
from src.pipeline.models import PipelineRun, PipelineStatus
from src.pipeline.orchestrator import PipelineOrchestrator

from .models import AgentTask, TaskStatus


class GoalPipelineBridge:
    """Bridges between the Goals system and the Pipeline Orchestrator.

    Responsibilities:
    - Convert an :class:`AgentTask` into an :class:`IntentDeclaration`.
    - Submit the intent to the pipeline and run it.
    - Report pipeline results back to update task status.
    """

    def __init__(self, orchestrator: PipelineOrchestrator) -> None:
        self._orchestrator = orchestrator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def task_to_intent(self, task: AgentTask, agent_id: str) -> IntentDeclaration:
        """Convert an :class:`AgentTask` into an :class:`IntentDeclaration`.

        The intent inherits the task's target files, services, and
        constraints, translating them into the schema the pipeline expects.
        """
        return IntentDeclaration(
            agent_id=agent_id,
            description=task.description,
            rationale=f"Task for goal {task.goal_id}: {task.title}",
            target_files=list(task.target_files),
            target_services=list(task.target_services),
            risk_hints={
                "estimated_risk": task.estimated_risk.value,
            },
            metadata={
                "task_id": str(task.task_id),
                "goal_id": str(task.goal_id),
                "constraints": task.constraints,
            },
        )

    def assign_task(self, task: AgentTask, agent_id: str) -> PipelineRun:
        """Convert a task to an intent and run it through the pipeline.

        Updates the task status to ASSIGNED before submission.

        Returns:
            The :class:`PipelineRun` produced by the orchestrator.
        """
        task.status = TaskStatus.ASSIGNED
        intent = self.task_to_intent(task, agent_id)
        pipeline_run = self._orchestrator.run(intent, agent_id)
        return pipeline_run

    def report_result(self, task: AgentTask, pipeline_run: PipelineRun) -> None:
        """Update task status based on pipeline outcome.

        Mapping:
        - PASSED  -> COMPLETED
        - FAILED  -> FAILED
        - BLOCKED -> IN_PROGRESS  (awaiting human approval)
        - other   -> IN_PROGRESS
        """
        if pipeline_run.status == PipelineStatus.PASSED:
            task.status = TaskStatus.COMPLETED
        elif pipeline_run.status == PipelineStatus.FAILED:
            task.status = TaskStatus.FAILED
        else:
            # BLOCKED or other intermediate states
            task.status = TaskStatus.IN_PROGRESS
