"""Unit tests for WebLogger (nova_sonic_demo/web/logger.py).

Validates Requirements 8.1, 8.2, 8.3, 8.4, 8.5.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nova_sonic_demo.web.logger import WebLogger, _safe_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeSender:
    """Collects messages sent via the async send_fn callback."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, msg: dict) -> None:
        self.messages.append(msg)


def _run(coro):
    """Run a coroutine in a fresh event loop (for tests without pytest-asyncio)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Tests: _safe_payload helper
# ---------------------------------------------------------------------------


class TestSafePayload:
    def test_serializable_dict(self) -> None:
        data = {"key": "value", "num": 42}
        assert _safe_payload(data) == data

    def test_serializable_list(self) -> None:
        data = [1, 2, 3]
        assert _safe_payload(data) == data

    def test_non_serializable_returns_sentinel(self) -> None:
        assert _safe_payload(object()) == "<non-serializable>"

    def test_non_serializable_set(self) -> None:
        assert _safe_payload({1, 2, 3}) == "<non-serializable>"

    def test_recursive_structure(self) -> None:
        d: dict[str, Any] = {}
        d["self"] = d
        assert _safe_payload(d) == "<non-serializable>"


# ---------------------------------------------------------------------------
# Tests: WebLogger inherits from ConsoleLogger (Requirement 8.1)
# ---------------------------------------------------------------------------


class TestWebLoggerInheritance:
    def test_is_subclass_of_console_logger(self) -> None:
        from nova_sonic_demo.logging import ConsoleLogger

        assert issubclass(WebLogger, ConsoleLogger)

    def test_has_session_gating(self) -> None:
        sender = FakeSender()
        logger = WebLogger(send_fn=sender)
        assert logger.is_session_active is False
        logger.mark_session_active()
        assert logger.is_session_active is True
        logger.mark_session_closed()
        assert logger.is_session_active is False


# ---------------------------------------------------------------------------
# Tests: _write suppresses stdout (Requirement 8.1)
# ---------------------------------------------------------------------------


class TestWriteSuppression:
    def test_write_does_not_output(self, capsys) -> None:
        sender = FakeSender()
        logger = WebLogger(send_fn=sender)
        logger._write("should not appear")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_banner_suppressed(self, capsys) -> None:
        sender = FakeSender()
        logger = WebLogger(send_fn=sender)
        logger.banner("model-id", "us-east-1")
        captured = capsys.readouterr()
        assert captured.out == ""


# ---------------------------------------------------------------------------
# Tests: tool_call sends correct message (Requirement 8.2)
# ---------------------------------------------------------------------------


class TestToolCall:
    def test_sends_tool_call_when_active(self) -> None:
        sender = FakeSender()
        logger = WebLogger(send_fn=sender)
        logger.mark_session_active()

        async def run():
            logger.tool_call("get_weather", {"city": "Seattle"})
            # Allow the ensure_future task to complete
            await asyncio.sleep(0)

        _run(run())
        assert len(sender.messages) == 1
        msg = sender.messages[0]
        assert msg == {
            "type": "tool_call",
            "name": "get_weather",
            "arguments": {"city": "Seattle"},
        }

    def test_suppressed_when_inactive(self) -> None:
        sender = FakeSender()
        logger = WebLogger(send_fn=sender)
        # session NOT active

        async def run():
            logger.tool_call("get_weather", {"city": "Seattle"})
            await asyncio.sleep(0)

        _run(run())
        assert len(sender.messages) == 0

    def test_non_serializable_arguments(self) -> None:
        sender = FakeSender()
        logger = WebLogger(send_fn=sender)
        logger.mark_session_active()

        async def run():
            logger.tool_call("broken_tool", object())
            await asyncio.sleep(0)

        _run(run())
        assert len(sender.messages) == 1
        assert sender.messages[0]["arguments"] == "<non-serializable>"


# ---------------------------------------------------------------------------
# Tests: tool_result sends correct message (Requirement 8.3)
# ---------------------------------------------------------------------------


class TestToolResult:
    def test_sends_tool_result_when_active(self) -> None:
        sender = FakeSender()
        logger = WebLogger(send_fn=sender)
        logger.mark_session_active()

        async def run():
            logger.tool_result("get_weather", {"temp": 72, "unit": "F"})
            await asyncio.sleep(0)

        _run(run())
        assert len(sender.messages) == 1
        msg = sender.messages[0]
        assert msg == {
            "type": "tool_result",
            "name": "get_weather",
            "result": {"temp": 72, "unit": "F"},
        }

    def test_suppressed_when_inactive(self) -> None:
        sender = FakeSender()
        logger = WebLogger(send_fn=sender)

        async def run():
            logger.tool_result("get_weather", {"temp": 72})
            await asyncio.sleep(0)

        _run(run())
        assert len(sender.messages) == 0

    def test_non_serializable_result(self) -> None:
        sender = FakeSender()
        logger = WebLogger(send_fn=sender)
        logger.mark_session_active()

        async def run():
            logger.tool_result("broken_tool", {1, 2, 3})
            await asyncio.sleep(0)

        _run(run())
        assert len(sender.messages) == 1
        assert sender.messages[0]["result"] == "<non-serializable>"
