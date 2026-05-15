"""Property-based test suite (P1-P7) for the Nova Sonic demo.

Each test maps to a property defined in ``design.md`` under
"Property-Based Testing Strategy" and to one or more acceptance criteria
in ``requirements.md``. The suite is intentionally fast: ``max_examples=30``
keeps each property under a second on a laptop, and ``deadline=None``
disables hypothesis's per-example deadline so a slow CI runner does not
flake.

Properties:

* P1 ``test_dispatcher_result_shape``
  Validates: Requirements 2.6, 3.4, 3.7, 3.8.
* P2 ``test_get_weather_deterministic_within_process``
  Validates: Requirements 2.5, 2.3.
* P3 ``test_get_current_time_shape_and_timezone_resolution``
  Validates: Requirements 2.2, 2.7.
* P4 ``test_logger_never_raises_on_arbitrary_payloads``
  Validates: Requirements 5.6, 5.7, 5.8.
* P5 ``test_logger_grammar``
  Validates: Requirements 5.4, 5.5, 5.6, 5.7.
* P6 ``test_session_close_idempotent_and_logger_quiet_after_close``
  Validates: Requirements 1.5, 5.2.
* P7 ``test_dispatcher_latency_bounds``
  Validates: Requirements 3.1, 3.2.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import sys
import zoneinfo
from typing import AsyncIterator

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from nova_sonic_demo.logging import ConsoleLogger
from nova_sonic_demo.tools import CONDITIONS
from nova_sonic_demo.tools.registry import (
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
    build_default_registry,
)
from nova_sonic_demo.tools.time_tool import get_current_time
from nova_sonic_demo.tools.weather_tool import get_weather


# ---------------------------------------------------------------------------
# Shared helpers and strategies
# ---------------------------------------------------------------------------


def _run(coro):
    """Run ``coro`` to completion on a fresh asyncio event loop."""
    return asyncio.run(coro)


def _active_logger() -> ConsoleLogger:
    logger = ConsoleLogger()
    logger.mark_session_active()
    return logger


_PRIMITIVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=20),
)

# Recursive JSON-shaped values: primitives, lists of children, dicts of children.
_JSON_LIKE = st.recursive(
    _PRIMITIVES,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=10), children, max_size=4),
    ),
    max_leaves=10,
)


# ---------------------------------------------------------------------------
# P1 -- dispatcher result shape
# ---------------------------------------------------------------------------


@given(
    tool_name=st.one_of(
        st.sampled_from(["get_current_time", "get_weather", "missing"]),
        st.text(min_size=1, max_size=20),
    ),
    arguments=st.dictionaries(
        st.text(min_size=1, max_size=8), _PRIMITIVES, max_size=5
    ),
)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_dispatcher_result_shape(tool_name, arguments):
    """P1: ``ToolDispatcher.dispatch`` always returns a well-shaped dict.

    Validates: Requirements 2.6, 3.4, 3.7, 3.8.
    """
    registry = build_default_registry()
    dispatcher = ToolDispatcher(registry, _active_logger())

    result = _run(dispatcher.dispatch("call-id", tool_name, arguments))

    assert isinstance(result, dict)
    if "error" in result:
        err = result["error"]
        assert isinstance(err, str)
        assert 1 <= len(err) <= 200
        if err == "unknown_tool":
            assert result.get("tool") == tool_name


# ---------------------------------------------------------------------------
# P2 -- get_weather is deterministic per process
# ---------------------------------------------------------------------------


@given(city=st.text(min_size=1, max_size=100).filter(lambda s: s.strip() != ""))
@settings(max_examples=30, deadline=None)
def test_get_weather_deterministic_within_process(city):
    """P2: same input -> same output; condition in fixed set; temperature bounded.

    Validates: Requirements 2.5, 2.3.
    """
    a = _run(get_weather({"city": city}))
    b = _run(get_weather({"city": city}))

    assert a == b
    assert a["condition"] in CONDITIONS
    assert isinstance(a["temperature_c"], int)
    assert -50 <= a["temperature_c"] <= 50


# ---------------------------------------------------------------------------
# P3 -- get_current_time shape and timezone resolution
# ---------------------------------------------------------------------------


@given(tz=st.one_of(st.none(), st.text(min_size=0, max_size=40)))
@settings(max_examples=30, deadline=None)
def test_get_current_time_shape_and_timezone_resolution(tz):
    """P3: valid IANA echoes back; missing -> UTC; invalid -> invalid_timezone.

    Validates: Requirements 2.2, 2.7.
    """
    args = {} if tz is None else {"timezone": tz}
    result = _run(get_current_time(args))

    if tz is None or tz == "":
        assert result["timezone"] == "UTC"
        assert "timestamp" in result
        return

    try:
        zoneinfo.ZoneInfo(tz)
        valid = True
    except Exception:
        valid = False

    if valid:
        assert result.get("timezone") == tz
        assert "timestamp" in result
    else:
        assert result == {"error": "invalid_timezone"}


# ---------------------------------------------------------------------------
# P4 -- logger never raises on arbitrary payloads
# ---------------------------------------------------------------------------


@given(name=st.text(min_size=1, max_size=20), payload=_JSON_LIKE)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_logger_never_raises_on_arbitrary_payloads(name, payload, capsys):
    """P4: ``tool_call``/``tool_result`` survive arbitrary JSON-shaped payloads.

    Validates: Requirements 5.6, 5.7, 5.8.
    """
    logger = _active_logger()

    logger.tool_call(name, payload)
    logger.tool_result(name, payload)

    out = capsys.readouterr().out
    # Two emissions, each ending in '\n'. ``name`` may itself contain
    # newlines (the grammar test enforces stricter shape); we only assert
    # here that nothing raised and the writes happened.
    assert out.count("\n") >= 2


def test_logger_handles_non_serializable_payloads_gracefully(capsys):
    """Non-serializable payloads are replaced by the literal ``<non-serializable>``.

    Targets the same property as P4 but with payload kinds hypothesis is
    unlikely to generate via JSON-shaped strategies (datetime, bytes,
    cyclic dicts).

    Validates: Requirement 5.8.
    """
    import datetime

    logger = _active_logger()

    logger.tool_call("t", {"when": datetime.datetime(2024, 1, 1)})
    logger.tool_result("t", b"\x00\x01")

    cyclic: dict = {}
    cyclic["self"] = cyclic
    logger.tool_call("t", cyclic)

    out = capsys.readouterr().out
    assert "TOOL_CALL: t <non-serializable>\n" in out
    assert "TOOL_RESULT: t <non-serializable>\n" in out


# ---------------------------------------------------------------------------
# P5 -- logger output grammar
# ---------------------------------------------------------------------------


GRAMMAR = re.compile(r"^(USER|ASSISTANT|TOOL_CALL|TOOL_RESULT): .+\n$")


@given(
    text=st.text(min_size=1, max_size=50).filter(lambda s: "\n" not in s),
    name=st.text(min_size=1, max_size=20).filter(
        lambda s: " " not in s and "\n" not in s
    ),
    payload=st.dictionaries(
        st.text(min_size=1, max_size=8).filter(lambda s: "\n" not in s),
        _PRIMITIVES,
        max_size=4,
    ),
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[
        HealthCheck.filter_too_much,
        HealthCheck.function_scoped_fixture,
    ],
)
def test_logger_grammar(text, name, payload, capsys):
    """P5: every emitted line matches the prefix grammar; tool payloads are
    single-line JSON or the literal placeholder.

    Validates: Requirements 5.4, 5.5, 5.6, 5.7.
    """
    logger = _active_logger()

    logger.user(text)
    logger.assistant(text)
    logger.tool_call(name, payload)
    logger.tool_result(name, payload)

    out = capsys.readouterr().out
    lines = [piece + "\n" for piece in out.split("\n") if piece]
    assert len(lines) == 4

    for line in lines:
        assert GRAMMAR.match(line), f"line did not match grammar: {line!r}"

    # The TOOL_CALL/TOOL_RESULT body (after ``<prefix>: <name> ``) is
    # either the literal ``<non-serializable>`` or single-line JSON.
    for line in lines[2:]:
        prefix, _, rest = line.rstrip("\n").partition(": ")
        assert prefix in {"TOOL_CALL", "TOOL_RESULT"}
        _toolname, _, body = rest.partition(" ")
        assert _toolname == name
        if body != "<non-serializable>":
            json.loads(body)  # Must parse cleanly.


# ---------------------------------------------------------------------------
# P6 -- session close is idempotent; logger is silent after close
# ---------------------------------------------------------------------------


class _FakeRpc:
    """Minimal bidirectional RPC stand-in for the session-close test."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_input(self, event: dict) -> None:
        await asyncio.sleep(0)
        self.sent.append(event)

    async def close_input(self) -> None:
        self.closed = True

    async def output(self) -> AsyncIterator[dict]:
        # Empty async generator: yields nothing.
        if False:  # pragma: no cover - intentional empty generator
            yield {}


class _FakeClient:
    def __init__(self, rpc: _FakeRpc) -> None:
        self._rpc = rpc

    async def invoke_model_with_bidirectional_stream(self, **_kwargs) -> _FakeRpc:
        return self._rpc


def test_session_close_idempotent_and_logger_quiet_after_close():
    """P6: repeated ``close()`` does not raise; post-close emissions no-op.

    Validates: Requirements 1.5, 5.2.
    """
    from nova_sonic_demo.session import SonicSession

    rpc = _FakeRpc()
    logger = _active_logger()
    registry = build_default_registry()
    dispatcher = ToolDispatcher(registry, logger)
    session = SonicSession(
        region="us-east-1",
        registry=registry,
        logger=logger,
        dispatcher=dispatcher,
        client_factory=lambda _r: _FakeClient(rpc),
        prompt_id_factory=lambda: "p",
        content_id_factory=lambda: "c",
        open_timeout_s=2.0,
    )

    async def scenario() -> None:
        await session.open()
        await session.close()
        await session.close()
        await session.close()

    _run(scenario())

    kinds = [next(iter(e["event"].keys())) for e in rpc.sent]
    # Terminator triple is emitted exactly once even though close() was
    # called three times.
    assert kinds.count("contentEnd") == 1
    assert kinds.count("promptEnd") == 1
    assert kinds.count("sessionEnd") == 1

    # After mark_session_closed, session-gated emissions must produce no
    # output. We swap sys.stdout for a StringIO so capsys does not affect
    # the assertion.
    logger.mark_session_closed()
    buf = io.StringIO()
    real_stdout = sys.stdout
    try:
        sys.stdout = buf
        logger.user("ignored")
        logger.assistant("ignored")
        logger.tool_call("t", {"x": 1})
        logger.tool_result("t", {"x": 1})
    finally:
        sys.stdout = real_stdout

    assert buf.getvalue() == ""


# ---------------------------------------------------------------------------
# P7 -- dispatcher latency bound
# ---------------------------------------------------------------------------


def test_dispatcher_latency_bounds():
    """P7: dispatch start -> handler entry and handler return -> dispatch end
    are each well under 0.5 s for a trivial handler.

    Validates: Requirements 3.1, 3.2.
    """
    import time

    handler_entry_t: list[float] = []
    handler_return_t: list[float] = []

    async def fast_handler(_args: dict) -> dict:
        handler_entry_t.append(time.monotonic())
        ret = {"ok": True}
        handler_return_t.append(time.monotonic())
        return ret

    schema = {"type": "object", "properties": {}, "required": []}
    registry = ToolRegistry(
        [
            ToolDefinition(
                name="latency",
                description="x",
                schema=schema,
                handler=fast_handler,
            )
        ]
    )
    logger = _active_logger()
    dispatcher = ToolDispatcher(registry, logger)

    async def scenario():
        start = time.monotonic()
        result = await dispatcher.dispatch("c1", "latency", {})
        end = time.monotonic()
        return start, end, result

    start, end, result = _run(scenario())

    assert result == {"ok": True}
    # Time from dispatch start to handler entry: validation + log only.
    assert handler_entry_t[0] - start < 0.5
    # Time from handler return to dispatcher end: log result + return.
    assert end - handler_return_t[0] < 0.5
