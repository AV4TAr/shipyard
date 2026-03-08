"""FastAPI application factory for the AI-CICD Command Center."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import agents, constraints, goals, pipeline, queue, status, ws


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AI-CICD Command Center",
        description="REST API wrapping the AI-native CI/CD pipeline.",
        version="0.1.0",
    )

    # CORS — allow all origins for dev
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(goals.router)
    app.include_router(pipeline.router)
    app.include_router(agents.router)
    app.include_router(status.router)
    app.include_router(queue.router)
    app.include_router(constraints.router)
    app.include_router(ws.router)

    return app
