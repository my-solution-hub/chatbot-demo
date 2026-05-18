"""Unit tests for ``nova_sonic_demo.web.messages``.

Covers:

* Dataclass construction for all server message types
* ``serialize_server_message`` produces valid JSON with correct fields
* ``parse_client_command`` accepts valid start/stop commands
* ``parse_client_command`` returns None for invalid inputs
* ``validate_audio_bytes`` accepts/rejects byte sequences correctly

Requirements: 3.3, 3.5, 7.1, 7.2, 7.3, 7.4
"""

from __future__ import annotations

import json

import pytest

from nova_sonic_demo.web.messages import (
    ClientCommand,
    ErrorMessage,
    ServerMessage,
    StartCommand,
    StatusMessage,
    StopCommand,
    ToolCallMessage,
    ToolResultMessage,
    TranscriptMessage,
    parse_client_command,
    serialize_server_message,
    validate_audio_bytes,
)


# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------


class TestTranscriptMessage:
    def test_user_role(self):
        msg = TranscriptMessage(role="USER", text="hello")
        assert msg.type == "transcript"
        assert msg.role == "USER"
        assert msg.text == "hello"

    def test_assistant_role(self):
        msg = TranscriptMessage(role="ASSISTANT", text="hi there")
        assert msg.type == "transcript"
        assert msg.role == "ASSISTANT"
        assert msg.text == "hi there"


class TestToolCallMessage:
    def test_basic(self):
        msg = ToolCallMessage(name="get_weather", arguments={"city": "NYC"})
        assert msg.type == "tool_call"
        assert msg.name == "get_weather"
        assert msg.arguments == {"city": "NYC"}


class TestToolResultMessage:
    def test_basic(self):
        msg = ToolResultMessage(name="get_weather", result={"temp": 72})
        assert msg.type == "tool_result"
        assert msg.name == "get_weather"
        assert msg.result == {"temp": 72}


class TestStatusMessage:
    @pytest.mark.parametrize("state", ["ready", "connecting", "active", "error", "closed"])
    def test_valid_states(self, state):
        msg = StatusMessage(state=state)
        assert msg.type == "status"
        assert msg.state == state


class TestErrorMessage:
    def test_basic(self):
        msg = ErrorMessage(message="something went wrong")
        assert msg.type == "error"
        assert msg.message == "something went wrong"


# ---------------------------------------------------------------------------
# serialize_server_message
# ---------------------------------------------------------------------------


class TestSerializeServerMessage:
    def test_transcript_message(self):
        msg = TranscriptMessage(role="USER", text="hello world")
        result = serialize_server_message(msg)
        data = json.loads(result)
        assert data == {"type": "transcript", "role": "USER", "text": "hello world"}

    def test_tool_call_message(self):
        msg = ToolCallMessage(name="time", arguments={"tz": "UTC"})
        result = serialize_server_message(msg)
        data = json.loads(result)
        assert data == {"type": "tool_call", "name": "time", "arguments": {"tz": "UTC"}}

    def test_tool_result_message(self):
        msg = ToolResultMessage(name="time", result={"time": "12:00"})
        result = serialize_server_message(msg)
        data = json.loads(result)
        assert data == {"type": "tool_result", "name": "time", "result": {"time": "12:00"}}

    def test_status_message(self):
        msg = StatusMessage(state="active")
        result = serialize_server_message(msg)
        data = json.loads(result)
        assert data == {"type": "status", "state": "active"}

    def test_error_message(self):
        msg = ErrorMessage(message="oops")
        result = serialize_server_message(msg)
        data = json.loads(result)
        assert data == {"type": "error", "message": "oops"}

    def test_unicode_text(self):
        msg = TranscriptMessage(role="ASSISTANT", text="こんにちは")
        result = serialize_server_message(msg)
        # ensure_ascii=False means unicode is preserved
        assert "こんにちは" in result
        data = json.loads(result)
        assert data["text"] == "こんにちは"

    def test_empty_arguments(self):
        msg = ToolCallMessage(name="no_args", arguments={})
        result = serialize_server_message(msg)
        data = json.loads(result)
        assert data["arguments"] == {}


# ---------------------------------------------------------------------------
# parse_client_command
# ---------------------------------------------------------------------------


class TestParseClientCommand:
    def test_start_command(self):
        cmd = parse_client_command('{"type": "start"}')
        assert isinstance(cmd, StartCommand)
        assert cmd.type == "start"

    def test_stop_command(self):
        cmd = parse_client_command('{"type": "stop"}')
        assert isinstance(cmd, StopCommand)
        assert cmd.type == "stop"

    def test_extra_fields_ignored(self):
        cmd = parse_client_command('{"type": "start", "extra": 123}')
        assert isinstance(cmd, StartCommand)

    def test_malformed_json_returns_none(self):
        assert parse_client_command("not json at all") is None

    def test_empty_string_returns_none(self):
        assert parse_client_command("") is None

    def test_missing_type_field_returns_none(self):
        assert parse_client_command('{"action": "start"}') is None

    def test_unrecognized_type_returns_none(self):
        assert parse_client_command('{"type": "pause"}') is None

    def test_null_type_returns_none(self):
        assert parse_client_command('{"type": null}') is None

    def test_numeric_type_returns_none(self):
        assert parse_client_command('{"type": 42}') is None

    def test_json_array_returns_none(self):
        assert parse_client_command('[{"type": "start"}]') is None

    def test_json_string_returns_none(self):
        assert parse_client_command('"start"') is None


# ---------------------------------------------------------------------------
# validate_audio_bytes
# ---------------------------------------------------------------------------


class TestValidateAudioBytes:
    def test_valid_two_bytes(self):
        assert validate_audio_bytes(b"\x00\x01") is True

    def test_valid_four_bytes(self):
        assert validate_audio_bytes(b"\x00\x01\x02\x03") is True

    def test_empty_bytes_invalid(self):
        assert validate_audio_bytes(b"") is False

    def test_odd_length_invalid(self):
        assert validate_audio_bytes(b"\x00\x01\x02") is False

    def test_single_byte_invalid(self):
        assert validate_audio_bytes(b"\x00") is False

    def test_large_even_valid(self):
        assert validate_audio_bytes(b"\x00" * 1024) is True

    def test_large_odd_invalid(self):
        assert validate_audio_bytes(b"\x00" * 1023) is False
