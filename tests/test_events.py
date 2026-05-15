"""Unit tests for ``nova_sonic_demo.events`` (task 4).

Covers:

* Each input-event builder returns the exact wire shape, including the
  ``{"event": {...}}`` envelope (Requirements 1.1, 2.1, 2.4, 3.1, 3.2).
* ``prompt_start_event`` carries the supplied tool configuration verbatim
  and conditionally adds a ``system`` key when ``system_prompt`` is
  non-empty.
* Every builder result is JSON-serializable (Req 5.6, 5.7 prelude).
* ``parse_output_event`` accepts both wrapped and unwrapped envelopes,
  decodes audio output, transcripts, and tool use events, and returns
  ``None`` (without raising) for unknown keys, missing fields, bad
  base64, and invalid JSON arguments.
"""

from __future__ import annotations

import base64
import json

import pytest

from nova_sonic_demo.events import (
    DEFAULT_INFERENCE_CONFIG,
    AudioOutEvent,
    ToolUseEvent,
    TranscriptEvent,
    audio_input_event,
    content_end_event,
    content_start_audio_input_event,
    content_start_text_input_event,
    content_start_tool_result_event,
    parse_output_event,
    prompt_end_event,
    prompt_start_event,
    session_end_event,
    session_start_event,
    text_input_event,
    tool_result_event,
)


# ---------------------------------------------------------------------------
# Input event builders -- exact-shape assertions
# ---------------------------------------------------------------------------


def test_default_inference_config_values():
    assert DEFAULT_INFERENCE_CONFIG == {
        "maxTokens": 1024,
        "topP": 0.9,
        "temperature": 0.7,
    }


def test_session_start_event_uses_default_inference_config_when_none():
    evt = session_start_event()
    assert evt == {
        "event": {
            "sessionStart": {
                "inferenceConfiguration": DEFAULT_INFERENCE_CONFIG,
            }
        }
    }


def test_session_start_event_uses_supplied_inference_config():
    custom = {"maxTokens": 256, "topP": 0.5, "temperature": 0.1}
    evt = session_start_event(custom)
    assert evt == {
        "event": {
            "sessionStart": {"inferenceConfiguration": custom},
        }
    }
    # The supplied dict is referenced (not copied).
    assert evt["event"]["sessionStart"]["inferenceConfiguration"] is custom


def test_prompt_start_event_without_system_prompt_has_documented_shape():
    tool_config = {
        "tools": [
            {
                "toolSpec": {
                    "name": "get_current_time",
                    "description": "Return the current ISO 8601 timestamp.",
                    "inputSchema": {"json": "{...}"},
                }
            }
        ]
    }

    evt = prompt_start_event("prompt-uuid", tool_config)

    assert evt == {
        "event": {
            "promptStart": {
                "promptName": "prompt-uuid",
                "textOutputConfiguration": {"mediaType": "text/plain"},
                "audioOutputConfiguration": {
                    "mediaType": "audio/lpcm",
                    "sampleRateHertz": 24000,
                    "sampleSizeBits": 16,
                    "channelCount": 1,
                    "voiceId": "matthew",
                    "encoding": "base64",
                    "audioType": "SPEECH",
                },
                "toolUseOutputConfiguration": {"mediaType": "application/json"},
                "toolConfiguration": tool_config,
            }
        }
    }
    # No 'system' key when no system prompt provided.
    assert "system" not in evt["event"]["promptStart"]


def test_prompt_start_event_carries_tool_config_verbatim():
    tool_config = {"tools": [{"a": 1}, {"b": 2}]}
    evt = prompt_start_event("p", tool_config)
    assert evt["event"]["promptStart"]["toolConfiguration"] is tool_config


def test_prompt_start_event_includes_system_when_non_empty():
    evt = prompt_start_event("p", {"tools": []}, system_prompt="be brief")
    assert evt["event"]["promptStart"]["system"] == "be brief"


def test_prompt_start_event_omits_system_when_empty_string():
    evt = prompt_start_event("p", {"tools": []}, system_prompt="")
    assert "system" not in evt["event"]["promptStart"]


def test_prompt_start_event_omits_system_when_none():
    evt = prompt_start_event("p", {"tools": []}, system_prompt=None)
    assert "system" not in evt["event"]["promptStart"]


def test_content_start_audio_input_event_exact_shape():
    evt = content_start_audio_input_event("p-uuid", "c-uuid")

    assert evt == {
        "event": {
            "contentStart": {
                "promptName": "p-uuid",
                "contentName": "c-uuid",
                "type": "AUDIO",
                "interactive": True,
                "role": "USER",
                "audioInputConfiguration": {
                    "mediaType": "audio/lpcm",
                    "sampleRateHertz": 16000,
                    "sampleSizeBits": 16,
                    "channelCount": 1,
                    "audioType": "SPEECH",
                    "encoding": "base64",
                },
            }
        }
    }


def test_audio_input_event_echoes_names_and_content():
    audio_b64 = base64.b64encode(b"\x01\x02\x03\x04").decode("ascii")

    evt = audio_input_event("p", "c", audio_b64)

    assert evt == {
        "event": {
            "audioInput": {
                "promptName": "p",
                "contentName": "c",
                "content": audio_b64,
            }
        }
    }


def test_content_end_event_exact_shape():
    evt = content_end_event("p", "c")
    assert evt == {
        "event": {"contentEnd": {"promptName": "p", "contentName": "c"}}
    }


def test_prompt_end_event_exact_shape():
    evt = prompt_end_event("p")
    assert evt == {"event": {"promptEnd": {"promptName": "p"}}}


def test_session_end_event_exact_shape():
    assert session_end_event() == {"event": {"sessionEnd": {}}}


# ---------------------------------------------------------------------------
# Tool-result helpers
# ---------------------------------------------------------------------------


def test_content_start_tool_result_event_exact_shape():
    evt = content_start_tool_result_event("p", "c", "tool-use-id-1")

    assert evt == {
        "event": {
            "contentStart": {
                "promptName": "p",
                "contentName": "c",
                "interactive": False,
                "type": "TOOL",
                "role": "TOOL",
                "toolResultInputConfiguration": {
                    "toolUseId": "tool-use-id-1",
                    "type": "TEXT",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                },
            }
        }
    }


def test_tool_result_event_exact_shape():
    payload = json.dumps({"timestamp": "2024-01-01T00:00:00+00:00"})
    evt = tool_result_event("p", "c", payload)
    assert evt == {
        "event": {
            "toolResult": {
                "promptName": "p",
                "contentName": "c",
                "content": payload,
            }
        }
    }


# ---------------------------------------------------------------------------
# JSON serializability for every builder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        lambda: session_start_event(),
        lambda: session_start_event({"maxTokens": 1, "topP": 0.1, "temperature": 0.0}),
        lambda: prompt_start_event("p", {"tools": []}),
        lambda: prompt_start_event("p", {"tools": []}, system_prompt="hi"),
        lambda: content_start_audio_input_event("p", "c"),
        lambda: audio_input_event("p", "c", "AAAA"),
        lambda: content_end_event("p", "c"),
        lambda: prompt_end_event("p"),
        lambda: session_end_event(),
        lambda: content_start_tool_result_event("p", "c", "tu"),
        lambda: tool_result_event("p", "c", '{"ok":true}'),
    ],
)
def test_each_builder_is_json_serializable(factory):
    evt = factory()
    # json.dumps must succeed without TypeError.
    serialized = json.dumps(evt)
    # And round-trip back to an equivalent dict.
    assert json.loads(serialized) == evt


# ---------------------------------------------------------------------------
# parse_output_event -- audioOutput
# ---------------------------------------------------------------------------


def test_parse_output_event_audio_round_trips_pcm_bytes():
    pcm = b"\x00\x01\x02\x03\x10\x20"
    encoded = base64.b64encode(pcm).decode("ascii")

    evt = parse_output_event(
        {"event": {"audioOutput": {"content": encoded}}}
    )

    assert isinstance(evt, AudioOutEvent)
    assert evt.pcm == pcm


def test_parse_output_event_audio_accepts_unwrapped_envelope():
    pcm = b"\xde\xad\xbe\xef"
    encoded = base64.b64encode(pcm).decode("ascii")

    evt = parse_output_event({"audioOutput": {"content": encoded}})

    assert isinstance(evt, AudioOutEvent)
    assert evt.pcm == pcm


def test_parse_output_event_audio_returns_none_for_bad_base64():
    assert parse_output_event(
        {"event": {"audioOutput": {"content": "!!!not-base64!!!"}}}
    ) is None


def test_parse_output_event_audio_returns_none_when_content_missing():
    assert parse_output_event({"event": {"audioOutput": {}}}) is None


def test_parse_output_event_audio_returns_none_when_content_not_string():
    assert parse_output_event(
        {"event": {"audioOutput": {"content": 123}}}
    ) is None


# ---------------------------------------------------------------------------
# parse_output_event -- textOutput
# ---------------------------------------------------------------------------


def test_parse_output_event_text_user_wrapped():
    evt = parse_output_event(
        {"event": {"textOutput": {"role": "USER", "content": "hello"}}}
    )

    assert evt == TranscriptEvent(role="USER", text="hello", is_final=True)


def test_parse_output_event_text_assistant_unwrapped():
    evt = parse_output_event(
        {"textOutput": {"role": "ASSISTANT", "content": "hi back"}}
    )

    assert evt == TranscriptEvent(role="ASSISTANT", text="hi back", is_final=True)


def test_parse_output_event_text_returns_none_for_unknown_role():
    assert parse_output_event(
        {"event": {"textOutput": {"role": "SYSTEM", "content": "ignore"}}}
    ) is None


def test_parse_output_event_text_returns_none_when_role_missing():
    assert parse_output_event(
        {"event": {"textOutput": {"content": "no role"}}}
    ) is None


def test_parse_output_event_text_returns_none_when_content_missing():
    assert parse_output_event(
        {"event": {"textOutput": {"role": "USER"}}}
    ) is None


# ---------------------------------------------------------------------------
# parse_output_event -- toolUse
# ---------------------------------------------------------------------------


def test_parse_output_event_tool_use_with_input_field():
    evt = parse_output_event(
        {
            "event": {
                "toolUse": {
                    "toolUseId": "tu-1",
                    "toolName": "get_weather",
                    "input": json.dumps({"city": "Seattle"}),
                }
            }
        }
    )

    assert evt == ToolUseEvent(
        tool_use_id="tu-1",
        tool_name="get_weather",
        arguments={"city": "Seattle"},
    )


def test_parse_output_event_tool_use_falls_back_to_arguments_field():
    evt = parse_output_event(
        {
            "toolUse": {
                "toolUseId": "tu-2",
                "toolName": "get_current_time",
                "arguments": json.dumps({"timezone": "UTC"}),
            }
        }
    )

    assert evt == ToolUseEvent(
        tool_use_id="tu-2",
        tool_name="get_current_time",
        arguments={"timezone": "UTC"},
    )


def test_parse_output_event_tool_use_returns_none_on_invalid_json():
    assert parse_output_event(
        {
            "event": {
                "toolUse": {
                    "toolUseId": "tu-3",
                    "toolName": "x",
                    "input": "not json{",
                }
            }
        }
    ) is None


def test_parse_output_event_tool_use_returns_none_when_required_fields_missing():
    # Missing toolUseId.
    assert parse_output_event(
        {"event": {"toolUse": {"toolName": "x", "input": "{}"}}}
    ) is None
    # Missing toolName.
    assert parse_output_event(
        {"event": {"toolUse": {"toolUseId": "tu", "input": "{}"}}}
    ) is None
    # Missing both input and arguments.
    assert parse_output_event(
        {"event": {"toolUse": {"toolUseId": "tu", "toolName": "x"}}}
    ) is None


def test_parse_output_event_tool_use_accepts_dict_input_directly():
    """Nova Sonic's SDK may surface tool args as a parsed dict; accept it."""
    evt = parse_output_event(
        {
            "event": {
                "toolUse": {
                    "toolUseId": "tu",
                    "toolName": "x",
                    "input": {"already": "decoded"},
                }
            }
        }
    )
    assert evt is not None
    assert evt.tool_name == "x"
    assert evt.arguments == {"already": "decoded"}


def test_parse_output_event_tool_use_returns_none_for_non_string_non_dict_input():
    assert parse_output_event(
        {
            "event": {
                "toolUse": {
                    "toolUseId": "tu",
                    "toolName": "x",
                    "input": 12345,
                }
            }
        }
    ) is None


def test_parse_output_event_tool_use_reads_content_field_first():
    """The Nova Sonic wire format puts arguments under ``content``."""
    evt = parse_output_event(
        {
            "event": {
                "toolUse": {
                    "toolUseId": "tu-content",
                    "toolName": "get_weather",
                    "content": '{"city":"Seattle"}',
                }
            }
        }
    )
    assert evt is not None
    assert evt.arguments == {"city": "Seattle"}


def test_parse_output_event_tool_use_returns_none_when_json_decodes_to_non_dict():
    assert parse_output_event(
        {
            "event": {
                "toolUse": {
                    "toolUseId": "tu",
                    "toolName": "x",
                    "input": "[1, 2, 3]",
                }
            }
        }
    ) is None


# ---------------------------------------------------------------------------
# parse_output_event -- unknown / malformed
# ---------------------------------------------------------------------------


def test_parse_output_event_unknown_key_returns_none():
    assert parse_output_event({"event": {"somethingElse": {}}}) is None
    assert parse_output_event({"unknown": {}}) is None


def test_parse_output_event_empty_dict_returns_none():
    assert parse_output_event({}) is None


def test_parse_output_event_event_value_not_dict_falls_through():
    # If "event" maps to a non-dict, treat the outer dict as already inner;
    # since the only key is "event" with a non-dict value, no known event
    # key is present and the function returns None.
    assert parse_output_event({"event": "garbage"}) is None


def test_parse_output_event_non_dict_input_returns_none():
    assert parse_output_event(None) is None  # type: ignore[arg-type]
    assert parse_output_event("not a dict") is None  # type: ignore[arg-type]
    assert parse_output_event(42) is None  # type: ignore[arg-type]


def test_parse_output_event_does_not_raise_on_arbitrary_payloads():
    # A grab-bag of malformed inputs that must each return None without raising.
    weird_inputs = [
        {"event": None},
        {"event": []},
        {"audioOutput": "not a dict"},
        {"textOutput": []},
        {"toolUse": 5},
        {"event": {"audioOutput": None}},
        {"event": {"textOutput": None}},
        {"event": {"toolUse": None}},
    ]
    for payload in weird_inputs:
        # Must not raise.
        assert parse_output_event(payload) is None


# ---------------------------------------------------------------------------
# Text input helpers (system prompt delivery)
# ---------------------------------------------------------------------------


def test_content_start_text_input_event_default_role_is_system():
    evt = content_start_text_input_event("p", "c")
    inner = evt["event"]["contentStart"]
    assert inner["promptName"] == "p"
    assert inner["contentName"] == "c"
    assert inner["type"] == "TEXT"
    assert inner["interactive"] is False
    assert inner["role"] == "SYSTEM"
    assert inner["textInputConfiguration"] == {"mediaType": "text/plain"}


def test_content_start_text_input_event_user_role_override():
    evt = content_start_text_input_event("p", "c", role="USER")
    assert evt["event"]["contentStart"]["role"] == "USER"


def test_text_input_event_carries_content_verbatim():
    evt = text_input_event("p", "c", "be brief")
    inner = evt["event"]["textInput"]
    assert inner == {"promptName": "p", "contentName": "c", "content": "be brief"}
