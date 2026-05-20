"""AgentCoreSessionManager — bridges a WebSocket connection to Bedrock AgentCore.

Manages the lifecycle of a single voice session bound to one WebSocket
connection, proxying audio and events to/from Bedrock AgentCore.

Supports two invocation modes:
1. AgentCore Runtime (new): invoke_agent_runtime with agentRuntimeArn
2. Legacy Bedrock Agent: invoke_agent with agentId/agentAliasId

The mode is auto-detected: if agent_id looks like an ARN
(contains "arn:aws:bedrock"), it uses invoke_agent_runtime.

Implements the same state machine as SessionManager:
ready → connecting → active (success) or ready → connecting → error (failure).
After stop(), transitions back to "ready" to allow restart.

Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 3.1, 3.2, 3.3, 3.4,
              4.1, 4.2, 4.3, 4.4, 4.5, 6.1, 6.2, 6.3
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Awaitable, Callable, Literal, Optional

from nova_sonic_demo.config import SHUTDOWN_DEADLINE_S
from nova_sonic_demo.web.messages import (
    ErrorMessage,
    StatusMessage,
    TranscriptMessage,
    ToolCallMessage,
    ToolResultMessage,
    serialize_server_message,
    validate_audio_bytes,
)

logger = logging.getLogger("nova_sonic_demo.web.agentcore_session_manager")

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SessionState = Literal["ready", "connecting", "active", "error"]

# Minimum session ID length required by AgentCore Runtime
_MIN_SESSION_ID_LEN = 33


def _is_runtime_arn(agent_id: str) -> bool:
    """Return True if agent_id is an AgentCore Runtime ARN."""
    return "arn:aws:bedrock" in agent_id and "runtime" in agent_id


class AgentCoreSessionManager:
    """Manages the lifecycle of an AgentCore streaming session bound to a WebSocket.

    Parameters
    ----------
    send_text:
        Async callable to send a text (JSON) message to the WebSocket client.
    send_bytes:
        Async callable to send binary data to the WebSocket client.
    agent_id:
        Either a Bedrock AgentCore Runtime ARN (new path) or a legacy agent ID.
    agent_alias_id:
        Agent alias ID (used in legacy mode; "DEFAULT" for runtime mode).
    region:
        AWS region for the AgentCore client.
    client_factory:
        Optional injectable factory for creating the boto3 client.
        Accepts (region,) and returns a client. Used for testing.
    """

    def __init__(
        self,
        send_text: Callable[[str], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
        *,
        agent_id: str,
        agent_alias_id: str,
        region: str,
        client_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._send_text = send_text
        self._send_bytes = send_bytes
        self._agent_id = agent_id
        self._agent_alias_id = agent_alias_id
        self._region = region
        self._client_factory = client_factory
        self._use_runtime = _is_runtime_arn(agent_id)

        self._state: SessionState = "ready"
        self._client: Any = None
        self._response_stream: Any = None
        self._session_id: Optional[str] = None

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
        """Open AgentCore streaming session.

        Transitions: ready → connecting → active (success)
                     ready → connecting → error (failure)
                     error → connecting → active (retry)
                     error → connecting → error (retry failure)
        """
        if self._state not in ("ready", "error"):
            return

        await self._transition("connecting")

        # Generate a unique session ID for this AgentCore session
        # AgentCore Runtime requires at least 33 characters
        self._session_id = uuid.uuid4().hex + uuid.uuid4().hex[:2]  # 34 chars

        # 1. Create the boto3 client
        try:
            if self._client_factory is not None:
                self._client = self._client_factory(self._region)
            else:
                import boto3  # noqa: WPS433

                if self._use_runtime:
                    self._client = boto3.client(
                        "bedrock-agentcore", region_name=self._region
                    )
                else:
                    self._client = boto3.client(
                        "bedrock-agent-runtime", region_name=self._region
                    )
        except Exception as exc:
            logger.error("Failed to create AgentCore client: %s", exc)
            await self._send_error(
                f"Failed to create AgentCore client: {type(exc).__name__}: {str(exc)[:200]}"
            )
            return

        # 2. Invoke the agent
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                self._invoke_agent,
            )
            if self._use_runtime:
                self._response_stream = response.get("response")
            else:
                self._response_stream = response.get("completion")
        except Exception as exc:
            error_category = _classify_error(exc)
            logger.error(
                "AgentCore invocation failed (%s): %s", error_category, exc
            )
            await self._send_error(
                f"Failed to connect to AgentCore: {error_category} - "
                f"{type(exc).__name__}: {str(exc)[:200]}"
            )
            return

        # 3. Transition to active
        await self._transition("active")
        logger.info(
            "AgentCore session is now ACTIVE (session_id=%s, runtime_mode=%s)",
            self._session_id,
            self._use_runtime,
        )

    def _invoke_agent(self) -> dict:
        """Synchronous agent invocation (run in executor)."""
        if self._use_runtime:
            # New AgentCore Runtime API
            payload = json.dumps({"prompt": ""}).encode("utf-8")
            return self._client.invoke_agent_runtime(
                agentRuntimeArn=self._agent_id,
                runtimeSessionId=self._session_id,
                payload=payload,
                qualifier="DEFAULT",
            )
        else:
            # Legacy Bedrock Agent API
            return self._client.invoke_agent(
                agentId=self._agent_id,
                agentAliasId=self._agent_alias_id,
                sessionId=self._session_id,
                enableTrace=False,
                inputText="",
            )

    async def handle_audio(self, pcm_bytes: bytes) -> None:
        """Forward validated PCM audio to AgentCore stream.

        Only forwards when the session is in 'active' state AND the
        audio bytes pass validation (len > 0, len % 2 == 0).

        Requirements: 2.5, 3.1, 3.2, 3.3, 3.4
        """
        if self._state != "active":
            return

        if not validate_audio_bytes(pcm_bytes):
            return

        if self._response_stream is not None:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._send_audio_chunk,
                    pcm_bytes,
                )
            except Exception as exc:
                logger.warning("Failed to send audio to AgentCore: %s", exc)

    def _send_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Synchronous audio send (run in executor)."""
        if hasattr(self._response_stream, "send_audio_event"):
            self._response_stream.send_audio_event(audio=pcm_bytes)
        elif hasattr(self._response_stream, "send"):
            self._response_stream.send({"audio": pcm_bytes})

    async def run_event_loop(self) -> None:
        """Consume AgentCore response stream, route events to WebSocket.

        Routes:
        - Audio events → binary WebSocket message (PCM bytes)
        - Transcript events → JSON WebSocket message
        - Tool call/result events → JSON WebSocket message

        The event loop runs until the AgentCore stream ends. Only unexpected
        exceptions trigger an error transition.

        Requirements: 2.6, 2.7, 2.8, 6.3
        """
        if self._response_stream is None:
            return

        try:
            if self._use_runtime:
                await self._consume_runtime_response()
            else:
                events = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._consume_stream,
                )
                for event in events:
                    await self._route_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._state == "active":
                logger.error("AgentCore stream error: %s", exc)
                await self._send_error(
                    f"AgentCore stream error: {type(exc).__name__}: {str(exc)[:200]}"
                )

    async def _consume_runtime_response(self) -> None:
        """Consume invoke_agent_runtime response.

        The response from invoke_agent_runtime is a StreamingBody.
        We read it and parse the JSON payload, then route events.
        """
        response_body = await asyncio.get_event_loop().run_in_executor(
            None,
            self._read_runtime_response,
        )

        if response_body is None:
            return

        # Parse the response — it may be a single JSON object or streaming chunks
        try:
            data = json.loads(response_body)
        except (json.JSONDecodeError, TypeError):
            # Not JSON — might be raw text response
            msg = serialize_server_message(
                TranscriptMessage(role="ASSISTANT", text=response_body)
            )
            await self._send_text(msg)
            return

        # Route based on response structure
        if isinstance(data, dict):
            # Single response object
            result_text = ""
            if "result" in data:
                result_text = str(data["result"])
            elif "output" in data:
                output = data["output"]
                if isinstance(output, dict) and "message" in output:
                    message = output["message"]
                    if isinstance(message, dict) and "content" in message:
                        content = message["content"]
                        if isinstance(content, list) and content:
                            result_text = content[0].get("text", str(content))
                        else:
                            result_text = str(content)
                    else:
                        result_text = str(message)
                else:
                    result_text = str(output)
            elif "message" in data:
                result_text = str(data["message"])
            else:
                result_text = json.dumps(data)

            if result_text:
                msg = serialize_server_message(
                    TranscriptMessage(role="ASSISTANT", text=result_text)
                )
                await self._send_text(msg)

    def _read_runtime_response(self) -> Optional[str]:
        """Read the streaming body from invoke_agent_runtime response."""
        if self._response_stream is None:
            return None

        if hasattr(self._response_stream, "read"):
            # StreamingBody
            return self._response_stream.read().decode("utf-8")
        elif isinstance(self._response_stream, bytes):
            return self._response_stream.decode("utf-8")
        elif isinstance(self._response_stream, str):
            return self._response_stream
        else:
            return str(self._response_stream)

    def _consume_stream(self) -> list:
        """Synchronous stream consumption for legacy mode (run in executor).

        Iterates over the response stream and collects events.
        """
        events = []
        try:
            if hasattr(self._response_stream, "__iter__"):
                for event in self._response_stream:
                    events.append(event)
        except Exception as exc:
            raise exc
        return events

    async def _route_event(self, event: dict) -> None:
        """Route a single AgentCore event to the appropriate WebSocket message."""
        if not isinstance(event, dict):
            return

        # Audio chunk
        if "chunk" in event:
            chunk = event["chunk"]
            if isinstance(chunk, dict) and "bytes" in chunk:
                audio_bytes = chunk["bytes"]
                if isinstance(audio_bytes, bytes):
                    await self._send_bytes(audio_bytes)
                return

        # Transcript
        if "transcript" in event:
            transcript = event["transcript"]
            if isinstance(transcript, dict):
                role = transcript.get("role", "ASSISTANT")
                text = transcript.get("text", "")
                msg = serialize_server_message(
                    TranscriptMessage(role=role, text=text)
                )
                await self._send_text(msg)
                return

        # Tool call
        if "tool_call" in event:
            tool_call = event["tool_call"]
            if isinstance(tool_call, dict):
                msg = serialize_server_message(
                    ToolCallMessage(
                        name=tool_call.get("name", ""),
                        arguments=tool_call.get("arguments", {}),
                    )
                )
                await self._send_text(msg)
                return

        # Tool result
        if "tool_result" in event:
            tool_result = event["tool_result"]
            if isinstance(tool_result, dict):
                msg = serialize_server_message(
                    ToolResultMessage(
                        name=tool_result.get("name", ""),
                        result=tool_result.get("result", {}),
                    )
                )
                await self._send_text(msg)
                return

    async def stop(self) -> None:
        """Close AgentCore session gracefully within shutdown deadline.

        Transitions: active → ready (allows restart)
                     connecting → ready
                     error → ready

        Requirements: 2.3, 2.4
        """
        if self._state == "ready":
            return

        # Close the stream/client within deadline
        if self._response_stream is not None:
            try:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, self._close_stream
                    ),
                    timeout=SHUTDOWN_DEADLINE_S,
                )
            except asyncio.TimeoutError:
                logger.warning("AgentCore session close exceeded deadline")
            except Exception as exc:
                logger.warning("AgentCore session close error: %s", exc)

        # Reset references
        self._response_stream = None
        self._client = None
        self._session_id = None

        await self._transition("ready")

    def _close_stream(self) -> None:
        """Synchronous stream close (run in executor)."""
        if self._response_stream is not None:
            if hasattr(self._response_stream, "close"):
                self._response_stream.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_error(exc: Exception) -> str:
    """Classify an AgentCore exception into a category string."""
    exc_name = type(exc).__name__

    if "AccessDenied" in exc_name or "Credentials" in exc_name:
        return "auth"
    if "ResourceNotFound" in exc_name or "NotFound" in exc_name:
        return "model"
    if "Timeout" in exc_name or "ConnectTimeout" in exc_name:
        return "timeout"
    if "Connection" in exc_name or "Endpoint" in exc_name or "Network" in exc_name:
        return "network"

    # Check message content as fallback
    msg = str(exc).lower()
    if "access denied" in msg or "credentials" in msg or "auth" in msg:
        return "auth"
    if "not found" in msg:
        return "model"
    if "timeout" in msg:
        return "timeout"
    if "connection" in msg or "network" in msg:
        return "network"

    return "unknown"


__all__ = ["AgentCoreSessionManager", "SessionState"]
