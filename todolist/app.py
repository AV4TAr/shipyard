"""FastAPI app for the shareable todo list."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

from .models import TodoStore

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Shareable Todo Lists")
store = TodoStore()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreateListRequest(BaseModel):
    title: str = "My Todo List"


class AddItemRequest(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.post("/api/lists")
def create_list(req: CreateListRequest):
    """Create a new todo list and return its unique ID."""
    todo = store.create_list(title=req.title)
    return {"list_id": todo.list_id, "url": f"/lists/{todo.list_id}"}


@app.get("/api/lists/{list_id}")
def get_list(list_id: str):
    """Get a todo list with all its items."""
    todo = store.get_list(list_id)
    if not todo:
        raise HTTPException(404, "List not found")
    return todo.model_dump()


@app.post("/api/lists/{list_id}/items")
def add_item(list_id: str, req: AddItemRequest):
    """Add an item to a list."""
    if not store.get_list(list_id):
        raise HTTPException(404, "List not found")
    item = store.add_item(list_id, req.text)
    return item.model_dump()


@app.patch("/api/lists/{list_id}/items/{item_id}")
def toggle_item(list_id: str, item_id: int):
    """Toggle an item's done status."""
    item = store.toggle_item(list_id, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    return item.model_dump()


@app.delete("/api/lists/{list_id}/items/{item_id}")
def delete_item(list_id: str, item_id: int):
    """Delete an item from a list."""
    if not store.delete_item(list_id, item_id):
        raise HTTPException(404, "Item not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Web UI — serve the SPA for any /lists/{id} URL
# ---------------------------------------------------------------------------

@app.get("/lists/{list_id}")
def serve_list_page(list_id: str):
    """Serve the frontend for a specific list."""
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.get("/")
def root():
    """Landing page — also serves the SPA (it handles 'no list' state)."""
    return FileResponse(str(_STATIC_DIR / "index.html"))


app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
