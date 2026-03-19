"""Models and storage for the shareable todo list app."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TodoItem(BaseModel):
    id: int = 0
    text: str
    done: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TodoList(BaseModel):
    list_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "My Todo List"
    items: list[TodoItem] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------

_DEFAULT_DB = Path(__file__).parent / "todos.db"


class TodoStore:
    """Simple SQLite-backed store for todo lists."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB) -> None:
        self._db_path = str(db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS lists (
                    list_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    list_id TEXT NOT NULL REFERENCES lists(list_id),
                    text TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )"""
            )

    # -- Lists --------------------------------------------------------------

    def create_list(self, title: str = "My Todo List") -> TodoList:
        todo = TodoList(title=title)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO lists (list_id, title, created_at) VALUES (?, ?, ?)",
                (todo.list_id, todo.title, todo.created_at),
            )
        return todo

    def get_list(self, list_id: str) -> TodoList | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM lists WHERE list_id = ?", (list_id,)
            ).fetchone()
            if not row:
                return None
            items = conn.execute(
                "SELECT * FROM items WHERE list_id = ? ORDER BY id", (list_id,)
            ).fetchall()
        return TodoList(
            list_id=row["list_id"],
            title=row["title"],
            created_at=row["created_at"],
            items=[
                TodoItem(id=i["id"], text=i["text"], done=bool(i["done"]), created_at=i["created_at"])
                for i in items
            ],
        )

    # -- Items --------------------------------------------------------------

    def add_item(self, list_id: str, text: str) -> TodoItem:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO items (list_id, text, done, created_at) VALUES (?, ?, 0, ?)",
                (list_id, text, now),
            )
        return TodoItem(id=cur.lastrowid, text=text, created_at=now)

    def toggle_item(self, list_id: str, item_id: int) -> TodoItem | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM items WHERE id = ? AND list_id = ?", (item_id, list_id)
            ).fetchone()
            if not row:
                return None
            new_done = not bool(row["done"])
            conn.execute("UPDATE items SET done = ? WHERE id = ?", (int(new_done), item_id))
        return TodoItem(id=row["id"], text=row["text"], done=new_done, created_at=row["created_at"])

    def delete_item(self, list_id: str, item_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM items WHERE id = ? AND list_id = ?", (item_id, list_id)
            )
        return cur.rowcount > 0
