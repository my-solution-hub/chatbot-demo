"""Unit tests for :class:`nova_sonic_demo.tools.registry.ToolDispatcher`.

Covers Requirements 2.6, 3.1, 3.2, 3.4, 3.5, 3.6, 3.7, 3.8, 5.6, 5.7.
"""

from __future__ import annotations

import asyncio
import io
import sys

import pytest

from nova_sonic_demo.logging import ConsoleLogger
from nova_sonic_demo.tools.registry import (
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
    build_default_registry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_logger() -> ConsoleLogger:
    logger = ConsoleLogger()
    logger.mark_session_active()
    return logger


def _run(coro):
    return asyncio.run(coro)


def _make_registry_with(name: str, handler, *, schema: dict | None = None) -> ToolRegistry:
    schema = schema if schema is not None else {
        "type": "object",
        "properties": {},
        "required": [],
    }
    return ToolRegistry(
        [
            ToolDefinition(
                name=name,
                description=f"test handler for {name}",
                schema=schema,
                handler=handler,
            )
        ]
    )


# ---------------------------------------------------------------------------
# 1. Successful dispatch through the default registry
# ---------------------------------------------------------------------------


def test_successful_dispatch_through_default_registry(capsys):
    registry = build_default_registry()
    dispatcher = ToolDispatcher(registry, _active_logger())

    result = _run(dispatcher.dispatch("call-1", "get_current_time", {}))

    assert result["timezone"] == "UTC"
    assert "timestamp" in result

    out = capsys.readouterr().out
    # Both lines should be present, in order.
    assert "TOOL_CALL: get_current_time {}" in out
    assert "TOOL_RESULT: get_current_time " in out
    assert out.index("TOOL_CALL:") < out.index("TOOL_RESULT:")


# ---------------------------------------------------------------------------
# 2. Unknown tool
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_unknown_tool_error(capsys):
    registry = build_default_registry()
    dispatcher = ToolDispatcher(registry, _active_logger())

    result = _run(dispatcher.dispatch("call-x", "nonexistent", {}))

    assert result == {"error": "unknown_tool", "tool": "nonexistent"}

    out = capsys.readouterr().out
    assert "TOOL_CALL: nonexistent {}" in out
    assert (
        'TOOL_RESULT: nonexistent {"error":"unknown_tool","tool":"nonexistent"}'
        in out
    )


# ---------------------------------------------------------------------------
# 3-6. Schema validation failures
# ---------------------------------------------------------------------------


def test_get_weather_missing_required_returns_invalid_arguments(capsys):
    dispatcher = ToolDispatcher(build_default_registry(), _active_logger())

    result = _run(dispatcher.dispatch("call-1", "get_weather", {}))

    assert result == {"error": "invalid_arguments"}
    out = capsys.readouterr().out
    assert "TOOL_CALL: get_weather {}" in out
    assert 'TOOL_RESULT: get_weather {"error":"invalid_arguments"}' in out


def test_get_weather_wrong_type_returns_invalid_arguments(capsys):
    dispatcher = ToolDispatcher(build_default_registry(), _active_logger())

    result = _run(dispatcher.dispatch("call-1", "get_weather", {"city": 123}))

    assert result == {"error": "invalid_arguments"}
    capsys.readouterr()


def test_get_weather_min_length_violation_returns_invalid_arguments():
    dispatcher = ToolDispatcher(build_default_registry(), _active_logger())

    result = _run(dispatcher.dispatch("call-1", "get_weather", {"city": ""}))

    assert result == {"error": "invalid_arguments"}


def test_get_weather_max_length_violation_returns_invalid_arguments():
    dispatcher = ToolDispatcher(build_default_registry(), _active_logger())

    result = _run(
        dispatcher.dispatch("call-1", "get_weather", {"city": "x" * 101})
    )

    assert result == {"error": "invalid_arguments"}


def test_get_weather_at_max_length_passes_validation():
    """Boundary check: 100 chars should validate (handler then runs)."""
    dispatcher = ToolDispatcher(build_default_registry(), _active_logger())

    result = _run(
        dispatcher.dispatch("call-1", "get_weather", {"city": "x" * 100})
    )

    # The handler ran; not an invalid_arguments error.
    assert result.get("error") != "invalid_arguments"


# ---------------------------------------------------------------------------
# 7. Handler exception
# ---------------------------------------------------------------------------


def test_handler_exception_returns_truncated_error_message():
    async def boom_handler(_args: dict) -> dict:
        raise RuntimeError("boom")

    registry = _make_registry_with("explode", boom_handler)
    dispatcher = ToolDispatcher(registry, _active_logger())

    result = _run(dispatcher.dispatch("call-1", "explode", {}))

    assert result == {"error": "boom"}


def test_handler_exception_message_truncated_to_200_chars():
    long_message = "a" * 500

    async def boom_handler(_args: dict) -> dict:
        raise RuntimeError(long_message)

    registry = _make_registry_with("explode_long", boom_handler)
    dispatcher = ToolDispatcher(registry, _active_logger())

    result = _run(dispatcher.dispatch("call-1", "explode_long", {}))

    assert "error" in result
    assert len(result["error"]) <= 200
    assert result["error"] == "a" * 200


# ---------------------------------------------------------------------------
# 8. Handler timeout
# ---------------------------------------------------------------------------


def test_handler_timeout_returns_tool_timeout_error():
    async def slow_handler(_args: dict) -> dict:
        await asyncio.sleep(10)
        return {"ok": True}

    registry = _make_registry_with("slow", slow_handler)
    dispatcher = ToolDispatcher(registry, _active_logger(), timeout_s=0.05)

    result = _run(dispatcher.dispatch("call-1", "slow", {}))

    assert result == {"error": "tool_timeout"}


# ---------------------------------------------------------------------------
# 9. TOOL_CALL is logged BEFORE handler runs
# ---------------------------------------------------------------------------


def test_tool_call_logged_before_handler_runs(monkeypatch):
    """When the handler enters, stdout already contains TOOL_CALL but not TOOL_RESULT."""
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    seen_at_handler_entry: dict[str, str] = {}

    async def recording_handler(_args: dict) -> dict:
        seen_at_handler_entry["snapshot"] = buf.getvalue()
        return {"ok": True}

    registry = _make_registry_with("record", recording_handler)
    dispatcher = ToolDispatcher(registry, _active_logger())

    _run(dispatcher.dispatch("call-1", "record", {}))

    snapshot = seen_at_handler_entry["snapshot"]
    assert "TOOL_CALL: record {}" in snapshot
    assert "TOOL_RESULT:" not in snapshot

    # After dispatch returns, TOOL_RESULT must also be present.
    final_output = buf.getvalue()
    assert "TOOL_RESULT: record " in final_output
    assert final_output.index("TOOL_CALL:") < final_output.index("TOOL_RESULT:")


# ---------------------------------------------------------------------------
# 10. Stateless across calls
# ---------------------------------------------------------------------------


def test_dispatcher_is_stateless_across_calls():
    dispatcher = ToolDispatcher(build_default_registry(), _active_logger())

    first = _run(dispatcher.dispatch("call-1", "nonexistent", {}))
    assert first == {"error": "unknown_tool", "tool": "nonexistent"}

    second = _run(dispatcher.dispatch("call-2", "get_current_time", {}))
    assert second.get("timezone") == "UTC"
    assert "error" not in second


def test_dispatcher_recovers_from_handler_exception_on_next_call():
    call_count = {"n": 0}

    async def flaky_handler(_args: dict) -> dict:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("first call fails")
        return {"ok": True, "n": call_count["n"]}

    registry = _make_registry_with("flaky", flaky_handler)
    dispatcher = ToolDispatcher(registry, _active_logger())

    first = _run(dispatcher.dispatch("c1", "flaky", {}))
    assert first == {"error": "first call fails"}

    second = _run(dispatcher.dispatch("c2", "flaky", {}))
    assert second == {"ok": True, "n": 2}


# ---------------------------------------------------------------------------
# 11. Session gating respected
# ---------------------------------------------------------------------------


def test_dispatch_runs_correctly_when_logger_session_inactive(capsys):
    logger = ConsoleLogger()  # NOT active
    assert logger.is_session_active is False

    dispatcher = ToolDispatcher(build_default_registry(), logger)

    result = _run(dispatcher.dispatch("call-1", "get_current_time", {}))

    assert result["timezone"] == "UTC"
    assert "timestamp" in result

    out = capsys.readouterr().out
    # Logger must suppress these lines while session is inactive.
    assert "TOOL_CALL:" not in out
    assert "TOOL_RESULT:" not in out


def test_dispatch_inactive_logger_still_returns_unknown_tool(capsys):
    logger = ConsoleLogger()
    dispatcher = ToolDispatcher(build_default_registry(), logger)

    result = _run(dispatcher.dispatch("call-1", "nonexistent", {}))

    assert result == {"error": "unknown_tool", "tool": "nonexistent"}
    assert capsys.readouterr().out == ""
