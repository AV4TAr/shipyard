"""FastAPI application factory for the Shipyard Command Center."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.sdk.routes import mount_sdk_routes

from .dependencies import get_runtime
from .routes import agents, config, constraints, goals, pipeline, projects, queue, routing, status, ws

_STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


async def _lease_sweep_loop(runtime, interval: float = 30.0) -> None:
    """Background task that periodically sweeps expired leases."""
    while True:
        try:
            await asyncio.sleep(interval)
            if runtime.lease_manager is not None:
                expired = runtime.lease_manager.sweep_expired()
                if expired:
                    logger.info("Lease sweep: reset %d expired tasks", len(expired))
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in lease sweep loop")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager — starts/stops the lease sweep loop."""
    runtime = get_runtime()

    # Start lease sweep background task
    sweep_task = None
    if runtime.lease_manager is not None:
        interval = runtime.lease_manager.sweep_interval_seconds
        sweep_task = asyncio.create_task(
            _lease_sweep_loop(runtime, interval=interval)
        )
        logger.info("Lease sweep loop started (interval=%ds)", interval)

    yield

    # Shutdown
    if sweep_task is not None:
        sweep_task.cancel()
        try:
            await sweep_task
        except asyncio.CancelledError:
            pass
        logger.info("Lease sweep loop stopped")


def create_app(runtime=None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        runtime: Optional CLIRuntime instance. When provided, it overrides the
            default singleton so tests can inject their own runtime.
    """
    app = FastAPI(
        title="Shipyard Command Center",
        description="REST API wrapping the Shipyard AI-native CI/CD pipeline.",
        version="0.1.0",
        lifespan=lifespan,
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
    app.include_router(config.router)
    app.include_router(constraints.router)
    app.include_router(projects.router)
    app.include_router(routing.router)
    app.include_router(ws.router)

    # Wire the EventDispatcher to WebSocket broadcasting
    rt = runtime or get_runtime()

    # Override the dependency so all Depends(get_runtime) calls use this runtime
    if runtime is not None:
        app.dependency_overrides[get_runtime] = lambda: runtime

    if rt.event_dispatcher is not None:
        ws.set_dispatcher(rt.event_dispatcher)

    # Mount SDK routes (agent submission endpoint)
    app.include_router(mount_sdk_routes(rt))

    # Agent status endpoint
    from .routes import agent_status
    app.include_router(agent_status.router)

    # Root route — serve the Command Center SPA
    @app.get("/")
    def root():
        """Serve the Command Center single-page application."""
        return FileResponse(str(_STATIC_DIR / "index.html"))

    # Mount static files (after API routers so /api/* takes priority)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app
