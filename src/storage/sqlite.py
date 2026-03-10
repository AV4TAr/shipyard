"""SQLite implementations of all repositories."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from src.goals.models import AgentTask, Goal, GoalPriority, GoalStatus
from src.intent.schema import IntentDeclaration
from src.pipeline.models import PipelineRun
from src.routing.models import AgentRegistration
from src.trust.models import AgentProfile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    status TEXT,
    priority TEXT,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    goal_id TEXT,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    status TEXT,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_profiles (
    agent_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_registrations (
    agent_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    """Create a connection and ensure tables exist."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


class SqliteGoalRepository:
    """SQLite-backed Goal repository."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = _connect(db_path)

    def save(self, goal: Goal) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO goals (id, status, priority, data) VALUES (?, ?, ?, ?)",
            (str(goal.goal_id), goal.status.value, goal.priority.value,
             goal.model_dump_json()),
        )
        self._conn.commit()

    def get(self, goal_id: uuid.UUID) -> Goal | None:
        row = self._conn.execute(
            "SELECT data FROM goals WHERE id = ?", (str(goal_id),)
        ).fetchone()
        if row is None:
            return None
        return Goal.model_validate_json(row[0])

    def list_all(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
    ) -> list[Goal]:
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority.value)
        query = "SELECT data FROM goals"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(query, params).fetchall()
        return [Goal.model_validate_json(r[0]) for r in rows]

    def delete(self, goal_id: uuid.UUID) -> None:
        self._conn.execute("DELETE FROM goals WHERE id = ?", (str(goal_id),))
        self._conn.commit()


class SqliteTaskRepository:
    """SQLite-backed AgentTask repository."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = _connect(db_path)

    def save(self, task: AgentTask) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO tasks (id, goal_id, data) VALUES (?, ?, ?)",
            (str(task.task_id), str(task.goal_id), task.model_dump_json()),
        )
        self._conn.commit()

    def get(self, task_id: uuid.UUID) -> AgentTask | None:
        row = self._conn.execute(
            "SELECT data FROM tasks WHERE id = ?", (str(task_id),)
        ).fetchone()
        if row is None:
            return None
        return AgentTask.model_validate_json(row[0])

    def list_by_goal(self, goal_id: uuid.UUID) -> list[AgentTask]:
        rows = self._conn.execute(
            "SELECT data FROM tasks WHERE goal_id = ?", (str(goal_id),)
        ).fetchall()
        return [AgentTask.model_validate_json(r[0]) for r in rows]


class SqlitePipelineRunRepository:
    """SQLite-backed PipelineRun repository."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = _connect(db_path)

    def save(self, run: PipelineRun) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO pipeline_runs (id, agent_id, status, data) "
            "VALUES (?, ?, ?, ?)",
            (str(run.run_id), run.agent_id, run.status.value,
             run.model_dump_json()),
        )
        self._conn.commit()

    def get(self, run_id: uuid.UUID) -> PipelineRun | None:
        row = self._conn.execute(
            "SELECT data FROM pipeline_runs WHERE id = ?", (str(run_id),)
        ).fetchone()
        if row is None:
            return None
        return PipelineRun.model_validate_json(row[0])

    def list_all(self, agent_id: str | None = None) -> list[PipelineRun]:
        if agent_id is not None:
            rows = self._conn.execute(
                "SELECT data FROM pipeline_runs WHERE agent_id = ?", (agent_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT data FROM pipeline_runs").fetchall()
        return [PipelineRun.model_validate_json(r[0]) for r in rows]


class SqliteAgentProfileRepository:
    """SQLite-backed AgentProfile repository."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = _connect(db_path)

    def save(self, profile: AgentProfile) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO agent_profiles (agent_id, data) VALUES (?, ?)",
            (profile.agent_id, profile.model_dump_json()),
        )
        self._conn.commit()

    def get(self, agent_id: str) -> AgentProfile | None:
        row = self._conn.execute(
            "SELECT data FROM agent_profiles WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        return AgentProfile.model_validate_json(row[0])

    def list_all(self) -> list[AgentProfile]:
        rows = self._conn.execute("SELECT data FROM agent_profiles").fetchall()
        return [AgentProfile.model_validate_json(r[0]) for r in rows]


class SqliteIntentRepository:
    """SQLite-backed IntentDeclaration repository."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = _connect(db_path)

    def save(self, intent: IntentDeclaration) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO intents (id, data) VALUES (?, ?)",
            (str(intent.intent_id), intent.model_dump_json()),
        )
        self._conn.commit()

    def get(self, intent_id: uuid.UUID) -> IntentDeclaration | None:
        row = self._conn.execute(
            "SELECT data FROM intents WHERE id = ?", (str(intent_id),)
        ).fetchone()
        if row is None:
            return None
        return IntentDeclaration.model_validate_json(row[0])

    def list_all(self) -> list[IntentDeclaration]:
        rows = self._conn.execute("SELECT data FROM intents").fetchall()
        return [IntentDeclaration.model_validate_json(r[0]) for r in rows]


class SqliteAgentRegistrationRepository:
    """SQLite-backed AgentRegistration repository."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = _connect(db_path)

    def save(self, registration: AgentRegistration) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO agent_registrations (agent_id, data) "
            "VALUES (?, ?)",
            (registration.agent_id, registration.model_dump_json()),
        )
        self._conn.commit()

    def get(self, agent_id: str) -> AgentRegistration | None:
        row = self._conn.execute(
            "SELECT data FROM agent_registrations WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            return None
        return AgentRegistration.model_validate_json(row[0])

    def list_all(self) -> list[AgentRegistration]:
        rows = self._conn.execute(
            "SELECT data FROM agent_registrations"
        ).fetchall()
        return [AgentRegistration.model_validate_json(r[0]) for r in rows]

    def delete(self, agent_id: str) -> None:
        self._conn.execute(
            "DELETE FROM agent_registrations WHERE agent_id = ?",
            (agent_id,),
        )
        self._conn.commit()
