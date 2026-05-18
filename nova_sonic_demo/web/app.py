"""FastAPI application for the Nova Sonic web UI.

Serves the static HTML/JS frontend and exposes a WebSocket endpoint
for bidirectional audio streaming and session control.

Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.4, 7.1, 7.2, 7.3
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from nova_sonic_demo.web.messages import parse_client_command, StartCommand, StopCommand
from nova_sonic_demo.web.session_manager import SessionManager

logger = logging.getLogger("nova_sonic_demo.web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

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
    """
    await ws.accept()

    # Helpers bound to this WebSocket connection
    async def _send_text(text: str) -> None:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_text(text)

    async def _send_bytes(data: bytes) -> None:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_bytes(data)

    manager = SessionManager(send_text=_send_text, send_bytes=_send_bytes)

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
