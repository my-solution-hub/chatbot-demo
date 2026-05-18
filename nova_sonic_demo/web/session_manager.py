"""SessionManager — bridges a WebSocket connection to a SonicSession.

Manages the lifecycle of a single voice session bound to one WebSocket
connection. Implements a state machine (ready → connecting → active → closed)
with error transitions from connecting/active states.

The SessionManager resolves AWS credentials and region, builds the tool
registry/dispatcher/session, opens the Bedrock stream, and routes audio
and transcript events between the WebSocket and the SonicSession.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.3, 3.4, 3.5, 4.1,
              5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Literal, Optional

from nova_sonic_demo.config import (
    SHUTDOWN_DEADLINE_S,
    BedrockOpenError,
    MissingCredentialsError,
    UnsupportedRegionError,
    assert_credentials_resolvable,
    resolve_region,
    validate_region,
)

logger = logging.getLogger("nova_sonic_demo.web.session_manager")
from nova_sonic_demo.events import AudioOutEvent, TranscriptEvent
from nova_sonic_demo.session import SonicSession
from nova_sonic_demo.tools.registry import ToolDispatcher, ToolRegistry, build_default_registry
from nova_sonic_demo.web.logger import WebLogger
from nova_sonic_demo.web.messages import (
    ErrorMessage,
    StatusMessage,
    TranscriptMessage,
    serialize_server_message,
    validate_audio_bytes,
)


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SessionState = Literal["ready", "connecting", "active", "error", "closed"]

# Default system prompt (same as CLI)
DEFAULT_SYSTEM_PROMPT = (
    "You are a friendly voice assistant. Keep replies short and natural. "
    "When the user asks about the time, call the get_current_time tool. "
    "When the user asks about the weather, call the get_weather tool. "
    "After a tool returns, summarize the result in one or two sentences."
)


class SessionManager:
    """Manages the lifecycle of a SonicSession bound to a WebSocket.

    Parameters
    ----------
    send_text:
        Async callable to send a text (JSON) message to the WebSocket client.
    send_bytes:
        Async callable to send binary data to the WebSocket client.
    session_factory:
        Optional injectable factory for creating a SonicSession. Accepts
        (region, registry, logger, dispatcher) and returns a SonicSession.
    registry_factory:
        Optional injectable factory for creating a ToolRegistry.
    dispatcher_factory:
        Optional injectable factory for creating a ToolDispatcher. Accepts
        (registry, logger) and returns a ToolDispatcher.
    """

    def __init__(
        self,
        send_text: Callable[[str], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
        *,
        session_factory: Optional[Callable[..., SonicSession]] = None,
        registry_factory: Optional[Callable[[], ToolRegistry]] = None,
        dispatcher_factory: Optional[Callable[..., ToolDispatcher]] = None,
    ) -> None:
        self._send_text = send_text
        self._send_bytes = send_bytes
        self._session_factory = session_factory
        self._registry_factory = registry_factory or build_default_registry
        self._dispatcher_factory = dispatcher_factory

        self._state: SessionState = "ready"
        self._session: Optional[SonicSession] = None
        self._logger: Optional[WebLogger] = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        """Current session state."""
        return self._state

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    async def _transition(self, new_state: SessionState) -> None:
        """Transition to a new state and send a status message."""
        self._state = new_state
        await self._send_status(new_state)

    async def _send_status(self, state: SessionState) -> None:
        """Send a status message to the WebSocket client."""
        msg = serialize_server_message(StatusMessage(state=state))
        await self._send_text(msg)

    async def _send_error(self, message: str) -> None:
        """Send an error message and transition to error state."""
        error_msg = serialize_server_message(ErrorMessage(message=message))
        await self._send_text(error_msg)
        await self._transition("error")

    # ------------------------------------------------------------------
    # Public lifecycle methods
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Resolve credentials, build session, open Bedrock stream.

        Transitions: ready → connecting → active (success)
                     ready → connecting → error (failure)
        """
        if self._state not in ("ready", "error"):
            return

        await self._transition("connecting")

        # 1. Resolve credentials
        try:
            assert_credentials_resolvable()
            logger.info("Credentials resolved OK")
        except MissingCredentialsError:
            logger.error("Missing AWS credentials")
            await self._send_error(
                "AWS credentials are not configured. Please set "
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY or "
                "configure a default profile."
            )
            return

        # 2. Resolve and validate region
        try:
            region = resolve_region()
            validate_region(region)
            logger.info("Region resolved: %s", region)
        except UnsupportedRegionError as exc:
            logger.error("Unsupported region: %s", exc)
            await self._send_error(str(exc))
            return

        # 3. Build registry, logger, dispatcher, and session
        registry = self._registry_factory()

        # Create a WebLogger that sends JSON dicts via our send_text callback
        async def _logger_send_fn(payload: dict) -> None:
            msg = serialize_server_message(
                _dict_to_server_message(payload)
            )
            await self._send_text(msg)

        self._logger = WebLogger(_logger_send_fn)

        if self._dispatcher_factory is not None:
            dispatcher = self._dispatcher_factory(registry, self._logger)
        else:
            dispatcher = ToolDispatcher(registry, self._logger)

        if self._session_factory is not None:
            self._session = self._session_factory(
                region, registry, self._logger, dispatcher
            )
        else:
            self._session = SonicSession(
                region=region,
                registry=registry,
                logger=self._logger,
                dispatcher=dispatcher,
                system_prompt=DEFAULT_SYSTEM_PROMPT,
            )

        # 4. Open the session
        try:
            logger.info("Opening Bedrock session...")
            await self._session.open()
            logger.info("Bedrock session opened successfully")
        except BedrockOpenError as exc:
            logger.error("Bedrock open failed: %s - %s", exc.category, exc.underlying)
            await self._send_error(
                f"Failed to connect to Bedrock: {exc.category} - {exc.underlying}"
            )
            return

        # 5. Mark logger active and transition to active state
        self._logger.mark_session_active()
        await self._transition("active")
        logger.info("Session is now ACTIVE")

    async def handle_audio(self, pcm_bytes: bytes) -> None:
        """Forward browser audio to SonicSession.send_audio().

        Only forwards when the session is in 'active' state AND the
        audio bytes pass validation (len > 0, len % 2 == 0).

        Requirements: 2.5, 3.3, 3.5, 6.6
        """
        if self._state != "active":
            return

        if not validate_audio_bytes(pcm_bytes):
            return

        if self._session is not None:
            await self._session.send_audio(pcm_bytes)

    async def run_event_loop(self) -> None:
        """Consume SonicSession.stream_events() and route to WebSocket.

        Routes:
        - AudioOutEvent → binary WebSocket message (PCM bytes)
        - TranscriptEvent → JSON WebSocket message

        The event loop runs until the Bedrock stream ends. A normal end
        (iterator exhaustion) is not an error — the session stays active
        and can still receive audio. Only unexpected exceptions trigger
        an error transition.

        Requirements: 2.6, 3.4, 4.1
        """
        if self._session is None:
            return

        try:
            async for event in self._session.stream_events():
                if isinstance(event, AudioOutEvent):
                    await self._send_bytes(event.pcm)
                elif isinstance(event, TranscriptEvent):
                    msg = serialize_server_message(
                        TranscriptMessage(role=event.role, text=event.text)
                    )
                    await self._send_text(msg)
        except asyncio.CancelledError:
            # Normal cancellation (e.g. user clicked Stop)
            raise
        except Exception as exc:
            # Stream errors while active transition to error state
            if self._state == "active":
                await self._send_error(
                    f"Session stream error: {type(exc).__name__}: {str(exc)[:200]}"
                )

    async def stop(self) -> None:
        """Close session gracefully within shutdown deadline.

        Transitions: active → ready (allows restart)
                     connecting → ready

        Requirements: 2.3, 2.4
        """
        if self._state in ("closed", "ready"):
            return

        # Mark logger closed before shutting down
        if self._logger is not None:
            self._logger.mark_session_closed()

        # Close session within deadline
        if self._session is not None:
            try:
                await asyncio.wait_for(
                    self._session.close(), timeout=SHUTDOWN_DEADLINE_S
                )
            except asyncio.TimeoutError:
                # Best-effort: deadline exceeded but we still transition
                pass
            except Exception:
                # Session close failed but we still transition
                pass

        # Reset session reference so a fresh one is created on next start()
        self._session = None
        self._logger = None
        await self._transition("ready")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dict_to_server_message(payload: dict):
    """Convert a WebLogger dict payload to a ServerMessage for serialization.

    The WebLogger emits dicts like {"type": "tool_call", "name": ..., "arguments": ...}
    and {"type": "tool_result", "name": ..., "result": ...}. We need to
    serialize these through our message protocol.
    """
    from nova_sonic_demo.web.messages import ToolCallMessage, ToolResultMessage

    msg_type = payload.get("type")
    if msg_type == "tool_call":
        return ToolCallMessage(
            name=payload.get("name", ""),
            arguments=payload.get("arguments", {}),
        )
    elif msg_type == "tool_result":
        return ToolResultMessage(
            name=payload.get("name", ""),
            result=payload.get("result", {}),
        )
    # Fallback: return an ErrorMessage for unknown types
    return ErrorMessage(message=f"Unknown logger event: {msg_type}")


__all__ = ["SessionManager", "SessionState"]
