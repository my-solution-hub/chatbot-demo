"""AgentCore stream event data models.

Dataclasses representing events received from Bedrock AgentCore's
bidirectional streaming API. Used by AgentCoreSessionManager to parse
and route stream events to the appropriate WebSocket message handlers.

Requirements: 2.6, 2.7, 2.8
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass
class AgentCoreAudioChunk:
    """Audio data received from AgentCore (PCM bytes)."""

    pcm_bytes: bytes


@dataclass
class AgentCoreTranscript:
    """Transcript event from AgentCore (user or assistant speech)."""

    role: str
    text: str


@dataclass
class AgentCoreToolCall:
    """Tool invocation notification from AgentCore."""

    tool_name: str
    arguments: dict


@dataclass
class AgentCoreToolResult:
    """Tool result notification from AgentCore."""

    tool_name: str
    result: dict


@dataclass
class AgentCoreSessionEnd:
    """Session ended by AgentCore."""

    reason: str


# Union type for all possible AgentCore events
AgentCoreEvent = Union[
    AgentCoreAudioChunk,
    AgentCoreTranscript,
    AgentCoreToolCall,
    AgentCoreToolResult,
    AgentCoreSessionEnd,
]


def parse_agentcore_event(event: dict) -> AgentCoreEvent | None:
    """Parse a raw AgentCore stream event dict into a typed dataclass.

    Parameters
    ----------
    event:
        A dictionary from the AgentCore response stream.

    Returns
    -------
    AgentCoreEvent or None
        A typed event dataclass, or None if the event is unrecognized.
    """
    if not isinstance(event, dict):
        return None

    # Audio chunk
    if "chunk" in event:
        chunk = event["chunk"]
        if isinstance(chunk, dict) and "bytes" in chunk:
            audio_bytes = chunk["bytes"]
            if isinstance(audio_bytes, bytes):
                return AgentCoreAudioChunk(pcm_bytes=audio_bytes)
        return None

    # Transcript
    if "transcript" in event:
        transcript = event["transcript"]
        if isinstance(transcript, dict):
            role = transcript.get("role", "ASSISTANT")
            text = transcript.get("text", "")
            return AgentCoreTranscript(role=role, text=text)
        return None

    # Tool call
    if "tool_call" in event:
        tool_call = event["tool_call"]
        if isinstance(tool_call, dict):
            return AgentCoreToolCall(
                tool_name=tool_call.get("name", ""),
                arguments=tool_call.get("arguments", {}),
            )
        return None

    # Tool result
    if "tool_result" in event:
        tool_result = event["tool_result"]
        if isinstance(tool_result, dict):
            return AgentCoreToolResult(
                tool_name=tool_result.get("name", ""),
                result=tool_result.get("result", {}),
            )
        return None

    # Session end
    if "session_end" in event:
        session_end = event["session_end"]
        if isinstance(session_end, dict):
            return AgentCoreSessionEnd(reason=session_end.get("reason", "unknown"))
        return AgentCoreSessionEnd(reason="unknown")

    return None


__all__ = [
    "AgentCoreAudioChunk",
    "AgentCoreTranscript",
    "AgentCoreToolCall",
    "AgentCoreToolResult",
    "AgentCoreSessionEnd",
    "AgentCoreEvent",
    "parse_agentcore_event",
]
