"""Application entry point.

Startup order matters and is deliberate: configuration is validated, then the role is
loaded, then providers are constructed. Every one of those can fail, and all of them
fail *here* — before the first robot connects — with a message naming what is wrong.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

from backend import __version__
from backend.communication import RobotConnection
from backend.config import Settings, get_settings
from backend.errors import ConfigError
from backend.logging_setup import configure_logging, log_event
from backend.pipeline import ConversationPipeline
from backend.protocol import PROTOCOL_VERSION
from backend.providers.http import close_client
from backend.providers.registry import ProviderBundle, build_providers
from backend.roles import list_roles, load_role_cached
from backend.roles.schema import RolePack

logger = logging.getLogger(__name__)


class AppState:
    """Process-wide singletons, built once at startup.

    An explicit object rather than module globals: it makes the dependencies of a
    connection visible, and it is what lets the tests build an app with fake providers
    without monkey-patching anything.
    """

    def __init__(self, settings: Settings, providers: ProviderBundle, role: RolePack) -> None:
        self.settings = settings
        self.providers = providers
        self.role = role
        self.pipeline = ConversationPipeline(providers, settings)


def build_state(settings: Settings | None = None) -> AppState:
    """Validate configuration and construct everything the app needs.

    Raises:
        ConfigError: with a message naming the setting at fault. This is the only place
            a misconfigured deployment should ever discover it is misconfigured.
    """
    settings = settings or get_settings()
    role = load_role_cached(settings.role_id, settings.roles_dir)
    providers = build_providers(settings)
    return AppState(settings, providers, role)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, log_transcripts=settings.log_transcripts)
    state = build_state(settings)
    app.state.fafobot = state

    log_event(
        logger,
        logging.INFO,
        "fafobot backend ready",
        version=__version__,
        protocol_version=PROTOCOL_VERSION,
        role=state.role.id,
        available_roles=list_roles(settings.roles_dir),
        providers=state.providers.describe(),
        log_transcripts=settings.log_transcripts,
    )
    try:
        yield
    finally:
        await close_client()


app = FastAPI(title="Fafobot Backend", version=__version__, lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness plus enough identity to tell two deployments apart."""
    state: AppState = app.state.fafobot
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "role": state.role.id,
            "providers": state.providers.describe(),
        }
    )


@app.get("/roles")
async def roles() -> JSONResponse:
    """Which roles this deployment could be switched to, and which is active.

    Read-only. Switching roles at runtime is deliberately not exposed: a role change
    resets every conversation, and V1 has no authentication to gate that behind.
    """
    state: AppState = app.state.fafobot
    return JSONResponse(
        {"active": state.role.id, "available": list_roles(state.settings.roles_dir)}
    )


@app.websocket("/ws/robot")
async def robot_socket(websocket: WebSocket) -> None:
    """The one endpoint a robot connects to."""
    state: AppState = app.state.fafobot
    await websocket.accept()
    connection = RobotConnection(
        websocket,
        role=state.role,
        pipeline=state.pipeline,
        settings=state.settings,
    )
    await connection.run()


def main() -> None:
    """Run the server. Entry point for `python -m backend.main`."""
    import uvicorn

    try:
        settings = get_settings()
    except ConfigError as exc:
        # Fail fast and readably. A traceback here helps nobody: the operator needs the
        # name of the setting they got wrong.
        raise SystemExit(f"Fafobot cannot start: {exc}") from exc

    configure_logging(settings.log_level, log_transcripts=settings.log_transcripts)
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
