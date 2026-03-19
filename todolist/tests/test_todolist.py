"""Tests for the shareable todo list app — models, API, and CLI."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from todolist.models import TodoItem, TodoList, TodoStore


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Return a path to a temporary SQLite database."""
    return tmp_path / "test_todos.db"


@pytest.fixture
def store(tmp_db: Path) -> TodoStore:
    """Create a TodoStore backed by a temp database."""
    return TodoStore(db_path=tmp_db)


@pytest.fixture
def client(tmp_db: Path):
    """Create a FastAPI TestClient with an isolated database."""
    from todolist import app as app_module

    original_store = app_module.store
    app_module.store = TodoStore(db_path=tmp_db)
    try:
        yield TestClient(app_module.app)
    finally:
        app_module.store = original_store


# ======================================================================
# Pydantic Model Tests
# ======================================================================


class TestModels:
    """Tests for Pydantic models."""

    def test_todo_item_defaults(self):
        item = TodoItem(text="Buy milk")
        assert item.id == 0
        assert item.text == "Buy milk"
        assert item.done is False
        assert item.created_at  # non-empty

    def test_todo_item_with_values(self):
        item = TodoItem(id=5, text="Walk dog", done=True)
        assert item.id == 5
        assert item.done is True

    def test_todo_list_defaults(self):
        todo = TodoList()
        assert todo.title == "My Todo List"
        assert len(todo.list_id) == 12
        assert todo.items == []

    def test_todo_list_custom_title(self):
        todo = TodoList(title="Groceries")
        assert todo.title == "Groceries"


# ======================================================================
# TodoStore Tests
# ======================================================================


class TestTodoStore:
    """Tests for SQLite-backed TodoStore."""

    def test_create_list(self, store: TodoStore):
        todo = store.create_list(title="Shopping")
        assert todo.title == "Shopping"
        assert len(todo.list_id) == 12
        assert todo.items == []

    def test_create_list_default_title(self, store: TodoStore):
        todo = store.create_list()
        assert todo.title == "My Todo List"

    def test_get_list(self, store: TodoStore):
        created = store.create_list(title="Work Tasks")
        fetched = store.get_list(created.list_id)
        assert fetched is not None
        assert fetched.list_id == created.list_id
        assert fetched.title == "Work Tasks"

    def test_get_nonexistent_list(self, store: TodoStore):
        result = store.get_list("nonexistent_id")
        assert result is None

    def test_add_item(self, store: TodoStore):
        todo = store.create_list()
        item = store.add_item(todo.list_id, "Buy groceries")
        assert item.text == "Buy groceries"
        assert item.done is False
        assert item.id > 0

    def test_add_multiple_items(self, store: TodoStore):
        todo = store.create_list()
        item1 = store.add_item(todo.list_id, "First")
        item2 = store.add_item(todo.list_id, "Second")
        assert item1.id != item2.id

        fetched = store.get_list(todo.list_id)
        assert len(fetched.items) == 2
        assert fetched.items[0].text == "First"
        assert fetched.items[1].text == "Second"

    def test_toggle_item(self, store: TodoStore):
        todo = store.create_list()
        item = store.add_item(todo.list_id, "Toggle me")

        toggled = store.toggle_item(todo.list_id, item.id)
        assert toggled is not None
        assert toggled.done is True

        toggled_back = store.toggle_item(todo.list_id, item.id)
        assert toggled_back.done is False

    def test_toggle_nonexistent_item(self, store: TodoStore):
        todo = store.create_list()
        result = store.toggle_item(todo.list_id, 9999)
        assert result is None

    def test_delete_item(self, store: TodoStore):
        todo = store.create_list()
        item = store.add_item(todo.list_id, "Delete me")

        assert store.delete_item(todo.list_id, item.id) is True
        fetched = store.get_list(todo.list_id)
        assert len(fetched.items) == 0

    def test_delete_nonexistent_item(self, store: TodoStore):
        todo = store.create_list()
        assert store.delete_item(todo.list_id, 9999) is False

    def test_delete_item_wrong_list(self, store: TodoStore):
        list1 = store.create_list(title="List 1")
        list2 = store.create_list(title="List 2")
        item = store.add_item(list1.list_id, "Belongs to list 1")

        # Should not delete item from wrong list
        assert store.delete_item(list2.list_id, item.id) is False

    def test_toggle_item_wrong_list(self, store: TodoStore):
        list1 = store.create_list(title="List 1")
        list2 = store.create_list(title="List 2")
        item = store.add_item(list1.list_id, "Belongs to list 1")

        result = store.toggle_item(list2.list_id, item.id)
        assert result is None

    def test_items_ordered_by_id(self, store: TodoStore):
        todo = store.create_list()
        store.add_item(todo.list_id, "A")
        store.add_item(todo.list_id, "B")
        store.add_item(todo.list_id, "C")

        fetched = store.get_list(todo.list_id)
        texts = [i.text for i in fetched.items]
        assert texts == ["A", "B", "C"]

    def test_multiple_lists_isolation(self, store: TodoStore):
        list1 = store.create_list(title="List 1")
        list2 = store.create_list(title="List 2")
        store.add_item(list1.list_id, "Item in list 1")
        store.add_item(list2.list_id, "Item in list 2")

        fetched1 = store.get_list(list1.list_id)
        fetched2 = store.get_list(list2.list_id)
        assert len(fetched1.items) == 1
        assert len(fetched2.items) == 1
        assert fetched1.items[0].text == "Item in list 1"
        assert fetched2.items[0].text == "Item in list 2"


# ======================================================================
# FastAPI Endpoint Tests
# ======================================================================


class TestAPI:
    """Tests for FastAPI REST endpoints."""

    def test_create_list(self, client: TestClient):
        resp = client.post("/api/lists", json={"title": "My Groceries"})
        assert resp.status_code == 200
        data = resp.json()
        assert "list_id" in data
        assert data["url"] == f"/lists/{data['list_id']}"

    def test_create_list_default_title(self, client: TestClient):
        resp = client.post("/api/lists", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "list_id" in data

    def test_get_list(self, client: TestClient):
        create_resp = client.post("/api/lists", json={"title": "Test List"})
        list_id = create_resp.json()["list_id"]

        resp = client.get(f"/api/lists/{list_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["list_id"] == list_id
        assert data["title"] == "Test List"
        assert data["items"] == []

    def test_get_nonexistent_list(self, client: TestClient):
        resp = client.get("/api/lists/doesnotexist")
        assert resp.status_code == 404

    def test_add_item(self, client: TestClient):
        create_resp = client.post("/api/lists", json={"title": "Test"})
        list_id = create_resp.json()["list_id"]

        resp = client.post(f"/api/lists/{list_id}/items", json={"text": "Buy milk"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Buy milk"
        assert data["done"] is False
        assert data["id"] > 0

    def test_add_item_nonexistent_list(self, client: TestClient):
        resp = client.post("/api/lists/nope/items", json={"text": "Fail"})
        assert resp.status_code == 404

    def test_toggle_item(self, client: TestClient):
        create_resp = client.post("/api/lists", json={"title": "Test"})
        list_id = create_resp.json()["list_id"]

        add_resp = client.post(f"/api/lists/{list_id}/items", json={"text": "Toggle me"})
        item_id = add_resp.json()["id"]

        resp = client.patch(f"/api/lists/{list_id}/items/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["done"] is True

        resp2 = client.patch(f"/api/lists/{list_id}/items/{item_id}")
        assert resp2.json()["done"] is False

    def test_toggle_nonexistent_item(self, client: TestClient):
        create_resp = client.post("/api/lists", json={"title": "Test"})
        list_id = create_resp.json()["list_id"]

        resp = client.patch(f"/api/lists/{list_id}/items/9999")
        assert resp.status_code == 404

    def test_delete_item(self, client: TestClient):
        create_resp = client.post("/api/lists", json={"title": "Test"})
        list_id = create_resp.json()["list_id"]

        add_resp = client.post(f"/api/lists/{list_id}/items", json={"text": "Delete me"})
        item_id = add_resp.json()["id"]

        resp = client.delete(f"/api/lists/{list_id}/items/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify it's gone
        list_resp = client.get(f"/api/lists/{list_id}")
        assert len(list_resp.json()["items"]) == 0

    def test_delete_nonexistent_item(self, client: TestClient):
        create_resp = client.post("/api/lists", json={"title": "Test"})
        list_id = create_resp.json()["list_id"]

        resp = client.delete(f"/api/lists/{list_id}/items/9999")
        assert resp.status_code == 404

    def test_full_workflow(self, client: TestClient):
        """End-to-end: create list, add items, toggle, delete, verify."""
        # Create list
        resp = client.post("/api/lists", json={"title": "Full Test"})
        list_id = resp.json()["list_id"]

        # Add items
        item1 = client.post(f"/api/lists/{list_id}/items", json={"text": "Task 1"}).json()
        item2 = client.post(f"/api/lists/{list_id}/items", json={"text": "Task 2"}).json()
        item3 = client.post(f"/api/lists/{list_id}/items", json={"text": "Task 3"}).json()

        # Toggle item 2
        client.patch(f"/api/lists/{list_id}/items/{item2['id']}")

        # Delete item 1
        client.delete(f"/api/lists/{list_id}/items/{item1['id']}")

        # Verify final state
        final = client.get(f"/api/lists/{list_id}").json()
        assert len(final["items"]) == 2
        texts = {i["text"]: i["done"] for i in final["items"]}
        assert texts == {"Task 2": True, "Task 3": False}


# ======================================================================
# CLI Tests
# ======================================================================


class TestCLI:
    """Tests for the CLI interface."""

    def test_new_command(self, tmp_db: Path, capsys):
        with patch("todolist.cli.TodoStore") as MockStore:
            mock_store = MockStore.return_value
            mock_list = TodoList(list_id="abc123def456", title="Test List")
            mock_store.create_list.return_value = mock_list

            from todolist.cli import main
            main(["new", "--title", "Test List"])

            mock_store.create_list.assert_called_once_with(title="Test List")
            output = capsys.readouterr().out
            assert "abc123def456" in output

    def test_add_command(self, capsys):
        with patch("todolist.cli.TodoStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.add_item.return_value = TodoItem(id=1, text="New item")

            from todolist.cli import main
            main(["add", "listid123456", "New item"])

            mock_store.add_item.assert_called_once_with("listid123456", "New item")
            output = capsys.readouterr().out
            assert "New item" in output

    def test_show_command(self, capsys):
        with patch("todolist.cli.TodoStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.get_list.return_value = TodoList(
                list_id="listid123456",
                title="My Tasks",
                items=[
                    TodoItem(id=1, text="Done task", done=True),
                    TodoItem(id=2, text="Open task", done=False),
                ],
            )

            from todolist.cli import main
            main(["show", "listid123456"])

            output = capsys.readouterr().out
            assert "My Tasks" in output
            assert "[x]" in output
            assert "[ ]" in output
            assert "Done task" in output
            assert "Open task" in output

    def test_show_nonexistent_list(self, capsys):
        with patch("todolist.cli.TodoStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.get_list.return_value = None

            from todolist.cli import main
            with pytest.raises(SystemExit) as exc_info:
                main(["show", "nonexistent00"])
            assert exc_info.value.code == 1

    def test_done_command(self, capsys):
        with patch("todolist.cli.TodoStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.toggle_item.return_value = TodoItem(id=1, text="Task", done=True)

            from todolist.cli import main
            main(["done", "listid123456", "1"])

            mock_store.toggle_item.assert_called_once_with("listid123456", 1)
            output = capsys.readouterr().out
            assert "done" in output

    def test_done_nonexistent_item(self, capsys):
        with patch("todolist.cli.TodoStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.toggle_item.return_value = None

            from todolist.cli import main
            with pytest.raises(SystemExit) as exc_info:
                main(["done", "listid123456", "999"])
            assert exc_info.value.code == 1

    def test_rm_command(self, capsys):
        with patch("todolist.cli.TodoStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.delete_item.return_value = True

            from todolist.cli import main
            main(["rm", "listid123456", "1"])

            mock_store.delete_item.assert_called_once_with("listid123456", 1)
            output = capsys.readouterr().out
            assert "Deleted" in output

    def test_rm_nonexistent_item(self, capsys):
        with patch("todolist.cli.TodoStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.delete_item.return_value = False

            from todolist.cli import main
            with pytest.raises(SystemExit) as exc_info:
                main(["rm", "listid123456", "999"])
            assert exc_info.value.code == 1

    def test_show_empty_list(self, capsys):
        with patch("todolist.cli.TodoStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.get_list.return_value = TodoList(
                list_id="listid123456",
                title="Empty List",
                items=[],
            )

            from todolist.cli import main
            main(["show", "listid123456"])

            output = capsys.readouterr().out
            assert "Empty List" in output
            assert "(empty)" in output
