"""AgentCore WebSocket Proxy — bridges browser WebSocket to AgentCore Runtime.

In cloud mode, the Fargate proxy connects to AgentCore Runtime's WebSocket
endpoint (wss://bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/ws)
using SigV4 authentication, and proxies messages bidirectionally.

The browser sends:
- JSON text messages (start/stop commands, config events)
- Binary PCM audio frames

The AgentCore Runtime agent (BidiAgent) sends:
- JSON events (audio, transcript, tool_call, tool_result, system, error)

This proxy translates between the browser's protocol and the AgentCore
Runtime's WebSocket protocol.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Optional

logger = logging.getLogger("nova_sonic_demo.web.agentcore_ws_proxy")


class AgentCoreWsProxy:
    """Proxies a browser WebSocket session to AgentCore Runtime via WebSocket.

    Parameters
    ----------
    runtime_arn:
        The AgentCore Runtime ARN to connect to.
    region:
        AWS region for the AgentCore endpoint.
    voice:
        Voice ID for Nova Sonic (default: tiffany).
    """

    def __init__(
        self,
        runtime_arn: str,
        region: str = "ap-northeast-1",
        voice: str = "tiffany",
    ) -> None:
        self._runtime_arn = runtime_arn
        self._region = region
        self._voice = voice
        self._ws: Any = None
        self._connected = False
        self._receive_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        """Connect to AgentCore Runtime WebSocket endpoint with SigV4."""
        import boto3
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from urllib.parse import urlparse

        try:
            import websockets
        except ImportError:
            raise RuntimeError(
                "websockets package required for AgentCore proxy. "
                "Add 'websockets' to requirements.txt"
            )

        # Build the WebSocket URL
        endpoint = f"wss://bedrock-agentcore.{self._region}.amazonaws.com"
        ws_url = f"{endpoint}/runtimes/{self._runtime_arn}/ws"

        # Sign the request with SigV4
        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is None:
            raise RuntimeError("AWS credentials not available")

        frozen_creds = credentials.get_frozen_credentials()

        # Create a request to sign
        parsed = urlparse(ws_url)
        # SigV4 signs an HTTPS GET request for WebSocket upgrade
        request = AWSRequest(
            method="GET",
            url=ws_url.replace("wss://", "https://"),
            headers={
                "host": parsed.hostname,
            },
        )

        SigV4Auth(frozen_creds, "bedrock-agentcore", self._region).add_auth(request)

        # Extract signed headers
        signed_headers = dict(request.headers)

        logger.info("Connecting to AgentCore Runtime WebSocket: %s", ws_url)

        self._ws = await websockets.connect(
            ws_url,
            additional_headers=signed_headers,
            max_size=None,  # No limit on message size
            ping_interval=30,
            ping_timeout=10,
        )
        self._connected = True
        logger.info("Connected to AgentCore Runtime WebSocket")

        # Send initial config event to the agent
        config_event = {
            "type": "config",
            "voice": self._voice,
            "input_sample_rate": 16000,
            "output_sample_rate": 16000,
            "model_id": "amazon.nova-2-sonic-v1:0",
            "region": self._region,
        }
        await self._ws.send(json.dumps(config_event))
        logger.info("Sent config event to agent")

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Send PCM audio to the agent as a base64-encoded audio event."""
        if not self._connected or self._ws is None:
            return

        audio_event = {
            "type": "audio_input",
            "audio": base64.b64encode(pcm_bytes).decode("ascii"),
        }
        try:
            await self._ws.send(json.dumps(audio_event))
        except Exception as exc:
            logger.warning("Failed to send audio to agent: %s", exc)

    async def send_text(self, text: str) -> None:
        """Send a text input to the agent."""
        if not self._connected or self._ws is None:
            return

        text_event = {
            "type": "text_input",
            "text": text,
        }
        try:
            await self._ws.send(json.dumps(text_event))
        except Exception as exc:
            logger.warning("Failed to send text to agent: %s", exc)

    async def receive_events(
        self,
        on_audio: Any,  # async callable(bytes)
        on_text: Any,   # async callable(str)
    ) -> None:
        """Receive events from the agent and route to browser.

        Routes:
        - Audio events → decode base64 → send as binary to browser
        - Transcript/tool/system/error events → send as JSON text to browser
        """
        if not self._connected or self._ws is None:
            return

        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    # Raw binary audio from agent
                    await on_audio(message)
                    continue

                # JSON text message
                try:
                    event = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    continue

                if not isinstance(event, dict):
                    continue

                event_type = event.get("type", "")

                # Audio event (base64 encoded)
                if event_type == "audio_output" or "audio" in event:
                    audio_b64 = event.get("audio", "")
                    if audio_b64:
                        try:
                            pcm = base64.b64decode(audio_b64)
                            await on_audio(pcm)
                        except Exception:
                            pass
                    continue

                # Transcript event
                if event_type == "transcript":
                    role = event.get("role", "ASSISTANT")
                    text = event.get("text", "")
                    msg = json.dumps({
                        "type": "transcript",
                        "role": role,
                        "text": text,
                    })
                    await on_text(msg)
                    continue

                # Tool call event
                if event_type == "tool_call":
                    msg = json.dumps({
                        "type": "tool_call",
                        "name": event.get("name", ""),
                        "arguments": event.get("arguments", {}),
                    })
                    await on_text(msg)
                    continue

                # Tool result event
                if event_type == "tool_result":
                    msg = json.dumps({
                        "type": "tool_result",
                        "name": event.get("name", ""),
                        "result": event.get("result", {}),
                    })
                    await on_text(msg)
                    continue

                # System/error/other — forward as-is
                if event_type in ("system", "error"):
                    await on_text(json.dumps(event))
                    continue

                # Unknown event type — forward raw
                await on_text(message)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._connected:
                logger.error("AgentCore WebSocket receive error: %s", exc)

    async def close(self) -> None:
        """Close the connection to AgentCore Runtime."""
        self._connected = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
