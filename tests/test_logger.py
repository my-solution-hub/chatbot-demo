"""Unit tests for :class:`nova_sonic_demo.logging.ConsoleLogger`.

Covers Requirements 5.1-5.8:

* 5.1 Startup banner contains model id and region.
* 5.2 Pre-session emissions of user/assistant/tool_call/tool_result are
      dropped at emission time without buffering.
* 5.3 ``LISTENING:`` line is printed once the session is active.
* 5.4 ``USER:`` prefix grammar.
* 5.5 ``ASSISTANT:`` prefix grammar.
* 5.6 ``TOOL_CALL:`` prefix grammar with single-line JSON arguments.
* 5.7 ``TOOL_RESULT:`` prefix grammar with single-line JSON result.
* 5.8 Non-serializable payloads are rendered as the literal
      ``<non-serializable>`` and never raise.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from nova_sonic_demo.logging import ConsoleLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_logger() -> ConsoleLogger:
    logger = ConsoleLogger()
    logger.mark_session_active()
    return logger


# ---------------------------------------------------------------------------
# Banner and listening (always-on)
# ---------------------------------------------------------------------------


def test_banner_prints_model_and_region_with_newline(capsys):
    logger = ConsoleLogger()  # session NOT active on purpose

    logger.banner("amazon.nova-2-sonic-v1:0", "us-east-1")

    captured = capsys.readouterr()
    assert captured.out == (
        "Nova Sonic Demo: model=amazon.nova-2-sonic-v1:0 region=us-east-1\n"
    )
    assert captured.err == ""


def test_banner_emits_even_when_session_inactive(capsys):
    """Requirement 5.1: banner must print at startup, before session active."""
    logger = ConsoleLogger()
    assert logger.is_session_active is False

    logger.banner("m", "r")

    assert capsys.readouterr().out == "Nova Sonic Demo: model=m region=r\n"


def test_listening_prefix_grammar(capsys):
    """Requirement 5.3: line starts with 'LISTENING: ' and ends with newline."""
    logger = _active_logger()

    logger.listening()

    out = capsys.readouterr().out
    assert out.startswith("LISTENING: ")
    assert out.endswith("\n")
    # No other newlines in the middle.
    assert out.count("\n") == 1


def test_listening_emits_even_when_session_inactive(capsys):
    """The CLI may emit LISTENING right at startup; gating must not suppress it."""
    logger = ConsoleLogger()

    logger.listening()

    assert capsys.readouterr().out.startswith("LISTENING: ")


# ---------------------------------------------------------------------------
# Session gating (Requirement 5.2)
# ---------------------------------------------------------------------------


def test_user_is_noop_before_session_active(capsys):
    logger = ConsoleLogger()

    logger.user("hello")

    assert capsys.readouterr().out == ""


def test_assistant_is_noop_before_session_active(capsys):
    logger = ConsoleLogger()

    logger.assistant("hi back")

    assert capsys.readouterr().out == ""


def test_tool_call_is_noop_before_session_active(capsys):
    logger = ConsoleLogger()

    logger.tool_call("get_weather", {"city": "Seattle"})

    assert capsys.readouterr().out == ""


def test_tool_result_is_noop_before_session_active(capsys):
    logger = ConsoleLogger()

    logger.tool_result("get_weather", {"city": "Seattle"})

    assert capsys.readouterr().out == ""


def test_pre_session_emissions_are_not_buffered(capsys):
    """Requirement 5.2: dropped at emission time, not deferred until active."""
    logger = ConsoleLogger()

    # Emit a barrage of events before the session is active. None of them
    # should appear after activation (no buffering, no replay).
    logger.user("buffered?")
    logger.assistant("buffered?")
    logger.tool_call("get_weather", {"city": "Seattle"})
    logger.tool_result("get_weather", {"city": "Seattle"})
    pre_active = capsys.readouterr().out
    assert pre_active == ""

    logger.mark_session_active()
    after_active = capsys.readouterr().out
    assert after_active == ""


def test_emissions_resume_after_mark_session_closed_then_active(capsys):
    logger = _active_logger()
    logger.user("first")
    assert capsys.readouterr().out == "USER: first\n"

    logger.mark_session_closed()
    logger.user("dropped")
    assert capsys.readouterr().out == ""

    logger.mark_session_active()
    logger.user("second")
    assert capsys.readouterr().out == "USER: second\n"


def test_mark_session_active_and_closed_are_idempotent(capsys):
    logger = ConsoleLogger()
    logger.mark_session_active()
    logger.mark_session_active()
    assert logger.is_session_active is True

    logger.mark_session_closed()
    logger.mark_session_closed()
    assert logger.is_session_active is False


# ---------------------------------------------------------------------------
# Exact-format assertions (Requirements 5.4, 5.5)
# ---------------------------------------------------------------------------


def test_user_exact_line_format(capsys):
    logger = _active_logger()

    logger.user("what time is it?")

    assert capsys.readouterr().out == "USER: what time is it?\n"


def test_assistant_exact_line_format(capsys):
    logger = _active_logger()

    logger.assistant("It is 3:14 PM UTC.")

    assert capsys.readouterr().out == "ASSISTANT: It is 3:14 PM UTC.\n"


def test_user_preserves_unicode(capsys):
    logger = _active_logger()

    logger.user("¿qué hora es?")

    assert capsys.readouterr().out == "USER: ¿qué hora es?\n"


# ---------------------------------------------------------------------------
# Tool call / tool result JSON serialization (Requirements 5.6, 5.7)
# ---------------------------------------------------------------------------


def test_tool_call_serializes_arguments_as_single_line_json(capsys):
    logger = _active_logger()

    logger.tool_call("get_weather", {"city": "Seattle"})

    out = capsys.readouterr().out
    assert out == 'TOOL_CALL: get_weather {"city":"Seattle"}\n'


def test_tool_result_serializes_result_as_single_line_json(capsys):
    logger = _active_logger()

    logger.tool_result(
        "get_weather",
        {"city": "Seattle", "condition": "rainy", "temperature_c": 12},
    )

    out = capsys.readouterr().out
    assert out == (
        'TOOL_RESULT: get_weather '
        '{"city":"Seattle","condition":"rainy","temperature_c":12}\n'
    )


def test_tool_call_empty_arguments(capsys):
    logger = _active_logger()

    logger.tool_call("get_current_time", {})

    assert capsys.readouterr().out == "TOOL_CALL: get_current_time {}\n"


def test_tool_call_json_is_single_line_for_nested_payload(capsys):
    logger = _active_logger()

    logger.tool_call("nested", {"a": [1, 2, {"b": "c"}]})

    out = capsys.readouterr().out
    # Exactly one newline (the trailing one).
    assert out.count("\n") == 1
    # Strip the prefix and trailing newline; the rest must be parseable JSON.
    expected_prefix = "TOOL_CALL: nested "
    assert out.startswith(expected_prefix)
    payload = out[len(expected_prefix):].rstrip("\n")
    assert json.loads(payload) == {"a": [1, 2, {"b": "c"}]}


# ---------------------------------------------------------------------------
# Non-serializable payloads (Requirement 5.8)
# ---------------------------------------------------------------------------


def test_tool_call_non_serializable_arguments_substitute_literal(capsys):
    logger = _active_logger()

    # ``datetime`` is not JSON-serializable by default.
    logger.tool_call("get_weather", {"when": datetime(2024, 1, 1, 12, 0, 0)})

    assert (
        capsys.readouterr().out == "TOOL_CALL: get_weather <non-serializable>\n"
    )


def test_tool_result_non_serializable_substitute_literal(capsys):
    logger = _active_logger()

    class _Opaque:
        pass

    logger.tool_result("custom", _Opaque())

    assert capsys.readouterr().out == "TOOL_RESULT: custom <non-serializable>\n"


def test_tool_call_non_serializable_does_not_raise():
    logger = _active_logger()

    # bytes are not JSON-serializable by default. Must not raise.
    logger.tool_call("opaque", b"\x00\x01")


def test_tool_result_non_serializable_does_not_raise():
    logger = _active_logger()

    cyclic: dict = {}
    cyclic["self"] = cyclic  # Cycles fail json.dumps with ValueError.

    logger.tool_result("opaque", cyclic)


# ---------------------------------------------------------------------------
# Stdout re-resolution (capsys compatibility)
# ---------------------------------------------------------------------------


def test_logger_uses_current_sys_stdout_at_call_time(capsys, monkeypatch):
    """The logger must look up sys.stdout per call so capsys can capture."""
    import io
    import sys

    logger = _active_logger()

    fake = io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake)
    logger.user("redirected")

    assert fake.getvalue() == "USER: redirected\n"
    # Nothing leaked to the real captured stdout.
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Grammar regex sanity (P5 prelude)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, expected_prefix",
    [
        ("user", "USER: "),
        ("assistant", "ASSISTANT: "),
    ],
)
def test_transcript_prefix_grammar(capsys, method, expected_prefix):
    logger = _active_logger()
    getattr(logger, method)("payload text")

    out = capsys.readouterr().out
    assert out.startswith(expected_prefix)
    assert out.endswith("\n")
    assert out.count("\n") == 1
