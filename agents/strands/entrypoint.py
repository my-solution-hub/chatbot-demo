"""Strands BidiAgent on AgentCore Runtime — WebSocket bidirectional streaming.

This agent runs on AgentCore Runtime and handles real-time voice conversations
using Nova Sonic via the Strands BidiAgent SDK. Tools are accessed through
AgentCore MCP Gateway.

AgentCore Runtime container contract:
- /ping GET — health check (required)
- /invocations POST — request-response fallback (required)
- /ws WebSocket — bidirectional audio streaming

Environment variables:
- MCP_GATEWAY_ARNS: JSON array of Gateway ARNs for tool access
- AWS_REGION: AWS region (default: us-east-1)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import traceback

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from strands.experimental.bidi.agent import BidiAgent
from strands.experimental.bidi.models import BidiNovaSonicModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("strands_agent")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# MCP Gateway ARNs — tools are accessed via AgentCore Gateway
_raw_gateway_arns = os.environ.get("MCP_GATEWAY_ARNS", "[]")
try:
    GATEWAY_ARNS = json.loads(_raw_gateway_arns)
except json.JSONDecodeError:
    GATEWAY_ARNS = []

SYSTEM_PROMPT = """You are a friendly voice assistant for a smart cat care system.
You can help users with:
- Looking up cat profiles and information
- Checking feeding schedules and recording feedings
- Monitoring health metrics and alerts
- Managing IoT devices (feeders, cameras, sensors)
- Telling the current time and weather

Always be friendly, concise, and helpful. When you use tools, summarize the
results in a natural conversational way."""

# ---------------------------------------------------------------------------
# Large-event splitting for WebSocket frame size limits
# ---------------------------------------------------------------------------

MAX_WS_MESSAGE_SIZE = 10000


def split_large_event(event_dict: dict, max_size: int = MAX_WS_MESSAGE_SIZE) -> list[dict]:
    """Split large audio events into smaller chunks for WebSocket transmission."""
    event_json = json.dumps(event_dict)
    event_size = len(event_json.encode("utf-8"))

    if event_size <= max_size:
        return [event_dict]

    if "audio" not in event_dict or not isinstance(event_dict["audio"], str):
        return [event_dict]

    audio_content = event_dict["audio"]
    template = {k: v for k, v in event_dict.items() if k != "audio"}
    template["audio"] = ""
    overhead = len(json.dumps(template).encode("utf-8"))

    max_content_size = max_size - overhead - 100
    # Align to 4-char boundaries for valid base64
    max_content_size = (max_content_size // 4) * 4

    if max_content_size <= 0:
        return [event_dict]

    chunks = []
    for i in range(0, len(audio_content), max_content_size):
        chunk_audio = audio_content[i: i + max_content_size]
        remainder = len(chunk_audio) % 4
        if remainder != 0:
            chunk_audio += "=" * (4 - remainder)
        chunk_event = {k: v for k, v in event_dict.items() if k != "audio"}
        chunk_event["audio"] = chunk_audio
        chunks.append(chunk_event)

    logger.info("Split event (%d bytes) into %d chunks", event_size, len(chunks))
    return chunks


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Chatbot Demo - Strands BidiAgent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    logger.info("Starting Strands BidiAgent server...")
    logger.info("Region: %s", AWS_REGION)
    logger.info("Gateway ARNs: %s", GATEWAY_ARNS)
    if not GATEWAY_ARNS:
        logger.warning("No MCP Gateway ARNs configured — agent will have no tools")


@app.get("/ping")
async def ping():
    """Health check endpoint required by AgentCore Runtime."""
    return JSONResponse({"status": "Healthy", "time_of_last_update": int(time.time())})


@app.post("/invocations")
async def invocations(request: dict = {}):
    """Request-response fallback. This agent is WebSocket-first."""
    return JSONResponse({
        "message": "This agent requires WebSocket connection for bidirectional audio streaming.",
        "websocket_endpoint": "/ws",
        "instructions": "Connect to /ws and send a 'config' event with voice settings.",
        "config_event_format": {
            "type": "config",
            "voice": "tiffany",
            "input_sample_rate": 16000,
            "output_sample_rate": 16000,
        },
    })


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Bidirectional audio streaming via WebSocket."""
    await websocket.accept()

    async def chunked_send_json(event_dict):
        """Send output events, splitting large audio payloads."""
        chunks = split_large_event(event_dict)
        for chunk in chunks:
            await websocket.send_json(chunk)

    await _handle_websocket_session(websocket, send_output=chunked_send_json)


# ---------------------------------------------------------------------------
# WebSocket session handler
# ---------------------------------------------------------------------------


async def _handle_websocket_session(
    websocket: WebSocket,
    send_output=None,
):
    """Handle a WebSocket session: wait for config, init agent, run."""
    agent = None
    output_fn = send_output or websocket.send_json

    logger.info("New WebSocket connection — waiting for config event...")

    try:
        # Wait for initial config event from client
        config = await _wait_for_config(websocket)
        if config is None:
            return

        # Create BidiAgent
        agent = _create_agent(config)
        logger.info("Agent initialized successfully")

        await websocket.send_json({
            "type": "system",
            "message": "Configuration applied. Agent ready.",
        })

        # Input handler — routes client messages to agent
        async def handle_websocket_input():
            while True:
                message = await websocket.receive_json()

                if message.get("type") == "config":
                    await websocket.send_json({
                        "type": "system",
                        "message": "Configuration can only be set once per session.",
                    })
                    continue
                elif message.get("type") == "text_input":
                    text = message.get("text", "")
                    logger.info("Received text input")
                    await agent.send(text)
                    continue
                else:
                    # Audio and other events — pass through to agent
                    return message

        # Run the agent with WebSocket I/O
        await agent.run(inputs=[handle_websocket_input], outputs=[output_fn])

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        if "InvalidStateError" in type(e).__name__ or "CANCELLED" in str(e):
            logger.warning("Ignoring CRT cleanup error")
        else:
            logger.error("Session error: %s", type(e).__name__)
            traceback.print_exc()
            try:
                await output_fn({"type": "error", "message": str(e)})
            except Exception:
                pass
    finally:
        logger.info("WebSocket session closed")


async def _wait_for_config(websocket: WebSocket) -> dict | None:
    """Wait for the initial config event from the client."""
    while True:
        message = await websocket.receive_json()
        if message.get("type") == "config":
            config = {
                "voice": message.get("voice", "tiffany"),
                "input_sample_rate": message.get("input_sample_rate", 16000),
                "output_sample_rate": message.get("output_sample_rate", 16000),
                "model_id": message.get("model_id", "amazon.nova-2-sonic-v1:0"),
                "region": message.get("region", AWS_REGION),
                "system_prompt": message.get("system_prompt", SYSTEM_PROMPT),
                "gateway_arns": message.get("gateway_arns", None),
            }
            logger.info(
                "Config received: voice=%s, model=%s, region=%s",
                config["voice"], config["model_id"], config["region"],
            )
            return config
        else:
            await websocket.send_json({
                "type": "system",
                "message": "Please send config event first",
            })


def _create_agent(config: dict) -> BidiAgent:
    """Create a BidiAgent from the session config."""
    # Use gateway ARNs from config if provided, otherwise use environment defaults
    effective_gateway_arns = config.get("gateway_arns") or GATEWAY_ARNS

    model = BidiNovaSonicModel(
        client_config={"region": config.get("region", AWS_REGION)},
        model_id=config["model_id"],
        provider_config={
            "audio": {
                "input_rate": config["input_sample_rate"],
                "output_rate": config["output_sample_rate"],
                "voice": config["voice"],
            }
        },
        mcp_gateway_arn=effective_gateway_arns if effective_gateway_arns else None,
    )

    return BidiAgent(
        model=model,
        tools=[],
        system_prompt=config.get("system_prompt", SYSTEM_PROMPT),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
