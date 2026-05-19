"""FastAPI application for the Nova Sonic web UI.

Serves the static HTML/JS frontend and exposes a WebSocket endpoint
for bidirectional audio streaming and session control.

Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.4, 7.1, 7.2, 7.3, 10.3
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from nova_sonic_demo.deployment_config import DeploymentConfig, load_config
from nova_sonic_demo.web.messages import parse_client_command, StartCommand, StopCommand
from nova_sonic_demo.web.session_factory import create_session_manager

logger = logging.getLogger("nova_sonic_demo.web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

try:
    _config = load_config()
except ValueError as exc:
    logger.warning("Failed to load deployment config, defaulting to local mode: %s", exc)
    _config = DeploymentConfig(mode="local", region="ap-northeast-1")


def _is_local_mode(config: DeploymentConfig) -> bool:
    """Return True if running in local mode (localhost/127.0.0.1)."""
    return config.mode == "local"


def _get_allowed_origins() -> Optional[list[str]]:
    """Load allowed origins from ALLOWED_ORIGINS env var (comma-separated).

    Returns None if the variable is not set or empty, meaning all origins
    are allowed (used in local mode).
    """
    raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
    if not raw:
        return None
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def validate_origin(origin: Optional[str], config: DeploymentConfig) -> bool:
    """Validate the Origin header for WebSocket connections.

    In local mode (127.0.0.1/localhost), all origins are allowed.
    In cloud mode, validates that Origin matches the expected CloudFront
    domain or is in the ALLOWED_ORIGINS list.

    Parameters
    ----------
    origin:
        The Origin header value from the WebSocket upgrade request.
    config:
        The deployment configuration.

    Returns
    -------
    bool
        True if the origin is valid, False otherwise.

    Requirements: 10.3
    """
    # Local mode: allow all origins
    if _is_local_mode(config):
        return True

    # Cloud mode: validate origin
    if origin is None:
        # No Origin header — reject in cloud mode
        return False

    allowed = _get_allowed_origins()

    # If ALLOWED_ORIGINS is not configured, allow all (fallback for dev)
    if allowed is None:
        return True

    # Check if origin matches any allowed origin
    for allowed_origin in allowed:
        if origin == allowed_origin:
            return True
        # Also match by hostname for flexibility
        try:
            parsed_origin = urlparse(origin)
            parsed_allowed = urlparse(allowed_origin)
            if (
                parsed_origin.hostname == parsed_allowed.hostname
                and parsed_origin.scheme == parsed_allowed.scheme
            ):
                return True
        except (ValueError, AttributeError):
            continue

    return False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Nova Sonic Demo")

# Mount static files so CSS/JS assets can be served directly
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> JSONResponse:
    """Health check endpoint.

    Returns HTTP 200 when the process is responsive.
    Does NOT depend on AgentCore connectivity.

    Requirements: 7.1, 7.2, 7.3
    """
    return JSONResponse(content={"status": "ok"}, status_code=200)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Serve the single-page HTML frontend."""
    index_path = _STATIC_DIR / "index.html"
    html_content = index_path.read_text(encoding="utf-8")
    return HTMLResponse(content=html_content)


@app.websocket("/ws/session")
async def websocket_session(ws: WebSocket) -> None:
    """Bidirectional audio + control WebSocket endpoint.

    Protocol:
    - Binary messages: raw PCM audio from the browser microphone
    - Text messages: JSON commands (start / stop)
    - Server sends binary (audio) and text (JSON status/transcript/tool) messages

    Requirements: 10.3 — validates Origin header before accepting connection.
    """
    # Validate Origin header to prevent cross-site WebSocket hijacking
    origin = ws.headers.get("origin")
    if not validate_origin(origin, _config):
        logger.warning("Rejected WebSocket connection with invalid origin: %s", origin)
        await ws.close(code=4003, reason="Origin not allowed")
        return

    await ws.accept()

    # Helpers bound to this WebSocket connection
    async def _send_text(text: str) -> None:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_text(text)

    async def _send_bytes(data: bytes) -> None:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_bytes(data)

    manager = create_session_manager(
        send_text=_send_text, send_bytes=_send_bytes, config=_config
    )

    # Send initial ready status
    from nova_sonic_demo.web.messages import StatusMessage, serialize_server_message

    await _send_text(serialize_server_message(StatusMessage(state="ready")))

    # Task for the event loop (consuming SonicSession events)
    event_loop_task: asyncio.Task | None = None

    try:
        while True:
            message = await ws.receive()

            if message["type"] == "websocket.receive":
                if "bytes" in message and message["bytes"]:
                    # Binary message → audio
                    await manager.handle_audio(message["bytes"])
                elif "text" in message and message["text"]:
                    # Text message → parse as command
                    command = parse_client_command(message["text"])
                    if isinstance(command, StartCommand):
                        logger.info("Received START command")
                        await manager.start()
                        logger.info("SessionManager state after start: %s", manager.state)
                        # Start the event loop concurrently
                        if manager.state == "active" and event_loop_task is None:
                            event_loop_task = asyncio.create_task(
                                manager.run_event_loop()
                            )
                            logger.info("Event loop task started")
                    elif isinstance(command, StopCommand):
                        logger.info("Received STOP command")
                        await manager.stop()
                        if event_loop_task is not None:
                            event_loop_task.cancel()
                            try:
                                await event_loop_task
                            except (asyncio.CancelledError, Exception):
                                pass
                            event_loop_task = None
            elif message["type"] == "websocket.disconnect":
                logger.info("WebSocket disconnect received")
                break

    except WebSocketDisconnect:
        logger.info("WebSocketDisconnect exception")
    except Exception as exc:
        logger.error("WebSocket handler error: %s: %s", type(exc).__name__, exc)
    finally:
        # Ensure cleanup on disconnect
        if event_loop_task is not None:
            event_loop_task.cancel()
            try:
                await event_loop_task
            except (asyncio.CancelledError, Exception):
                pass
        await manager.stop()
        logger.info("WebSocket session cleaned up")
