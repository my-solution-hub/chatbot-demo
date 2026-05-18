"""WebSocket message types and validation for the web UI protocol.

Defines the data models for server-to-browser messages and browser-to-server
commands, along with serialization, parsing, and validation utilities.

Requirements: 3.3, 3.5, 7.1, 7.2, 7.3, 7.4
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Literal, Optional, Union


# ---------------------------------------------------------------------------
# Server → Browser message types
# ---------------------------------------------------------------------------


@dataclass
class TranscriptMessage:
    """Real-time transcript of user or assistant speech."""

    role: Literal["USER", "ASSISTANT"]
    text: str
    type: Literal["transcript"] = "transcript"


@dataclass
class ToolCallMessage:
    """Notification that a tool was invoked."""

    name: str
    arguments: dict
    type: Literal["tool_call"] = "tool_call"


@dataclass
class ToolResultMessage:
    """Result returned from a tool invocation."""

    name: str
    result: dict
    type: Literal["tool_result"] = "tool_result"


@dataclass
class StatusMessage:
    """Session state change notification."""

    state: Literal["ready", "connecting", "active", "error", "closed"]
    type: Literal["status"] = "status"


@dataclass
class ErrorMessage:
    """Error description sent to the browser."""

    message: str
    type: Literal["error"] = "error"


ServerMessage = Union[
    TranscriptMessage,
    ToolCallMessage,
    ToolResultMessage,
    StatusMessage,
    ErrorMessage,
]


# ---------------------------------------------------------------------------
# Browser → Server command types
# ---------------------------------------------------------------------------


@dataclass
class StartCommand:
    """Request to open a new voice session."""

    type: Literal["start"] = "start"


@dataclass
class StopCommand:
    """Request to close the current voice session."""

    type: Literal["stop"] = "stop"


ClientCommand = Union[StartCommand, StopCommand]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def serialize_server_message(msg: ServerMessage) -> str:
    """Convert a ServerMessage dataclass to a JSON string.

    Uses ``dataclasses.asdict`` for conversion and ``json.dumps`` for
    serialization with compact separators.
    """
    return json.dumps(asdict(msg), separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_client_command(text: str) -> Optional[ClientCommand]:
    """Parse a JSON text message from the browser into a ClientCommand.

    Returns ``None`` for:
    - Malformed JSON (Requirement 7.3)
    - Missing ``type`` field (Requirement 7.1)
    - Unrecognized ``type`` value (Requirement 7.2)
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    msg_type = data.get("type")
    if msg_type == "start":
        return StartCommand()
    elif msg_type == "stop":
        return StopCommand()
    else:
        return None


# ---------------------------------------------------------------------------
# Audio validation
# ---------------------------------------------------------------------------


def validate_audio_bytes(data: bytes) -> bool:
    """Check that binary audio data is valid PCM (16-bit samples).

    Valid audio must have:
    - Length greater than zero (Requirement 7.4)
    - Length that is a multiple of 2 (16-bit samples) (Requirement 3.3)

    Returns True if valid, False otherwise.
    """
    return len(data) > 0 and len(data) % 2 == 0
