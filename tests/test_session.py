"""Integration tests for :class:`nova_sonic_demo.session.SonicSession`.

These tests run entirely against a ``FakeRpc`` that records every event
emitted on the input stream and yields a queued sequence of canned output
events. No real AWS calls are made; ``boto3`` is never reached because we
inject a ``client_factory`` that returns a fake client.

Covered scenarios (per task 8 of the spec):

* ``test_open_sends_session_start_prompt_start_content_start_in_order``
  -- canonical opener handshake order and stable prompt/content names.
* ``test_open_timeout_raises_bedrock_open_error_with_timeout_category``
  -- a slow invoke maps to ``BedrockOpenError(category="timeout")``.
* ``test_send_audio_emits_audio_input_event_with_base64_payload``
  -- audio frames are base64-encoded into ``audioInput`` events.
* ``test_tool_use_event_triggers_dispatch_and_emits_tool_result_sequence``
  -- a ``toolUse`` output event causes the dispatcher to run and the
  session to emit a ``contentStart`` (TOOL) / ``toolResult`` /
  ``contentEnd`` triple within ~0.5 s.
* ``test_close_is_idempotent_and_sends_session_terminators``
  -- ``close()`` emits the terminator triple exactly once.
* ``test_send_audio_after_close_is_noop_or_raises_cleanly``
  -- post-close ``send_audio`` raises ``RuntimeError('session closed')``.
* ``test_concurrent_send_audio_and_send_tool_result_serialize``
  -- the internal write lock prevents tool-result events from being
  interleaved by concurrent audio sends.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
from typing import AsyncIterator, Optional

import pytest

from nova_sonic_demo.config import BedrockOpenError
from nova_sonic_demo.logging import ConsoleLogger
from nova_sonic_demo.session import SonicSession
from nova_sonic_demo.tools.registry import ToolDispatcher, build_default_registry


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRpc:
    """Records every event sent and yields the canned output sequence.

    The hint in the task description proposes this exact shape; we follow
    it to keep the tests easy to read.
    """

    def __init__(
        self,
        output_events: Optional[list[dict]] = None,
        *,
        open_delay: float = 0.0,
    ) -> None:
        self._output_events: list[dict] = list(output_events or [])
        self._sent: list[dict] = []
        self._closed_input = False
        self._open_delay = open_delay

    async def send_input(self, event: dict) -> None:
        # Yield once so two concurrent senders have a chance to interleave
        # if the session's write lock is missing or broken.
        await asyncio.sleep(0)
        self._sent.append(event)

    async def close_input(self) -> None:
        self._closed_input = True

    async def output(self) -> AsyncIterator[dict]:
        for ev in list(self._output_events):
            await asyncio.sleep(0)
            yield ev

    @property
    def sent(self) -> list[dict]:
        return list(self._sent)

    @property
    def closed_input(self) -> bool:
        return self._closed_input


class FakeClient:
    """Minimal stand-in for a boto3 bedrock-runtime client."""

    def __init__(self, rpc: FakeRpc, *, invoke_delay: float = 0.0) -> None:
        self._rpc = rpc
        self._invoke_delay = invoke_delay
        self.invoked_with: list[dict] = []

    async def invoke_model_with_bidirectional_stream(self, **kwargs) -> FakeRpc:
        self.invoked_with.append(kwargs)
        if self._invoke_delay:
            await asyncio.sleep(self._invoke_delay)
        return self._rpc


def _make_session(
    rpc: FakeRpc,
    *,
    invoke_delay: float = 0.0,
    open_timeout_s: float = 5.0,
    prompt_name: str = "p-fixed",
    content_name_seed: str = "c",
    system_prompt: str | None = None,
) -> SonicSession:
    registry = build_default_registry()
    logger = ConsoleLogger()
    dispatcher = ToolDispatcher(registry, logger, timeout_s=2.0)

    counter = itertools.count(1)

    def content_factory() -> str:
        return f"{content_name_seed}-{next(counter)}"

    return SonicSession(
        region="us-east-1",
        registry=registry,
        logger=logger,
        dispatcher=dispatcher,
        client_factory=lambda _region: FakeClient(rpc, invoke_delay=invoke_delay),
        prompt_id_factory=lambda: prompt_name,
        content_id_factory=content_factory,
        open_timeout_s=open_timeout_s,
        system_prompt=system_prompt,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inner(event: dict) -> dict:
    """Return the inner event payload, stripping the ``{"event": {...}}`` envelope."""
    assert "event" in event, f"missing event envelope: {event!r}"
    inner = event["event"]
    assert isinstance(inner, dict)
    assert len(inner) == 1, f"event must have exactly one inner key, got {list(inner)!r}"
    return inner


def _event_kind(event: dict) -> str:
    return next(iter(_inner(event).keys()))


def _event_payload(event: dict) -> dict:
    return next(iter(_inner(event).values()))


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Opener handshake
# ---------------------------------------------------------------------------


def test_open_sends_session_start_prompt_start_content_start_in_order():
    rpc = FakeRpc()
    session = _make_session(rpc)

    _run(session.open())

    sent = rpc.sent
    assert len(sent) == 3, f"expected 3 opener events, got {len(sent)}: {sent!r}"

    assert _event_kind(sent[0]) == "sessionStart"
    assert _event_kind(sent[1]) == "promptStart"
    assert _event_kind(sent[2]) == "contentStart"

    # promptStart carries the tool configuration produced by the registry.
    prompt_start = _event_payload(sent[1])
    assert "toolConfiguration" in prompt_start
    tool_config = prompt_start["toolConfiguration"]
    assert {t["toolSpec"]["name"] for t in tool_config["tools"]} == {
        "get_current_time",
        "get_weather",
    }
    assert prompt_start["promptName"] == "p-fixed"

    # contentStart opens the AUDIO USER turn under the same prompt name.
    content_start = _event_payload(sent[2])
    assert content_start["type"] == "AUDIO"
    assert content_start["role"] == "USER"
    assert content_start["promptName"] == "p-fixed"
    # The first content name is consumed for the audio input turn.
    assert content_start["contentName"] == "c-1"


def test_open_passes_model_id_to_invoke():
    rpc = FakeRpc()
    fake_client_holder = {}

    def factory(_region: str) -> FakeClient:
        client = FakeClient(rpc)
        fake_client_holder["client"] = client
        return client

    registry = build_default_registry()
    logger = ConsoleLogger()
    dispatcher = ToolDispatcher(registry, logger)
    session = SonicSession(
        region="us-east-1",
        registry=registry,
        logger=logger,
        dispatcher=dispatcher,
        client_factory=factory,
        prompt_id_factory=lambda: "p",
        content_id_factory=lambda: "c",
        open_timeout_s=2.0,
    )

    _run(session.open())

    client = fake_client_holder["client"]
    assert len(client.invoked_with) == 1
    kwargs = client.invoked_with[0]
    assert kwargs["modelId"] == "amazon.nova-2-sonic-v1:0"


# ---------------------------------------------------------------------------
# 2. Open timeout
# ---------------------------------------------------------------------------


def test_open_timeout_raises_bedrock_open_error_with_timeout_category():
    rpc = FakeRpc()
    # A 10-second invoke delay against a 0.05-second open deadline.
    session = _make_session(rpc, invoke_delay=10.0, open_timeout_s=0.05)

    with pytest.raises(BedrockOpenError) as excinfo:
        _run(session.open())

    assert excinfo.value.category == "timeout"


# ---------------------------------------------------------------------------
# 3. send_audio base64 payload
# ---------------------------------------------------------------------------


def test_send_audio_emits_audio_input_event_with_base64_payload():
    rpc = FakeRpc()
    session = _make_session(rpc)

    async def scenario() -> None:
        await session.open()
        await session.send_audio(b"\x00\x01\x02")

    _run(scenario())

    audio_inputs = [e for e in rpc.sent if _event_kind(e) == "audioInput"]
    assert len(audio_inputs) == 1
    payload = _event_payload(audio_inputs[0])
    expected_b64 = base64.b64encode(b"\x00\x01\x02").decode("ascii")
    assert payload["content"] == expected_b64
    # The audio event reuses the prompt and content names from open().
    assert payload["promptName"] == "p-fixed"
    assert payload["contentName"] == "c-1"


# ---------------------------------------------------------------------------
# 4. Tool use round-trip
# ---------------------------------------------------------------------------


def test_tool_use_event_triggers_dispatch_and_emits_tool_result_sequence():
    rpc = FakeRpc(
        output_events=[
            {
                "event": {
                    "toolUse": {
                        "toolUseId": "tu-42",
                        "toolName": "get_current_time",
                        "input": "{}",
                    }
                }
            }
        ]
    )
    session = _make_session(rpc)

    async def scenario() -> None:
        await session.open()

        async def consume() -> list:
            collected = []
            async for evt in session.stream_events():
                collected.append(evt)
            return collected

        # The output stream contains only a toolUse, so stream_events()
        # yields nothing and returns once the queue is drained.
        await asyncio.wait_for(consume(), timeout=0.5)

        # Wait for the in-flight tool task to finish writing its result.
        # Bound the wait to keep the latency requirement honest (~0.5s).
        deadline = asyncio.get_event_loop().time() + 0.5
        while asyncio.get_event_loop().time() < deadline:
            kinds = [_event_kind(e) for e in rpc.sent]
            if (
                "toolResult" in kinds
                and kinds.count("contentEnd") >= 1
                and any(
                    _event_kind(e) == "contentStart"
                    and _event_payload(e).get("type") == "TOOL"
                    for e in rpc.sent
                )
            ):
                break
            await asyncio.sleep(0.01)

    _run(scenario())

    # Filter the TOOL-related events in emission order.
    tool_events = [
        e
        for e in rpc.sent
        if (
            (_event_kind(e) == "contentStart" and _event_payload(e).get("type") == "TOOL")
            or _event_kind(e) == "toolResult"
            or (
                _event_kind(e) == "contentEnd"
                and _event_payload(e).get("contentName", "").startswith("c-")
                # Skip the opener content's audio contentEnd if it ever appears.
                and _event_payload(e).get("contentName") != "c-1"
            )
        )
    ]

    kinds = [_event_kind(e) for e in tool_events]
    assert kinds == ["contentStart", "toolResult", "contentEnd"], (
        f"expected TOOL contentStart/toolResult/contentEnd, got {kinds!r}"
    )

    # contentStart for the tool result references the right tool_use_id.
    cs_payload = _event_payload(tool_events[0])
    assert cs_payload["type"] == "TOOL"
    assert cs_payload["role"] == "TOOL"
    assert cs_payload["toolResultInputConfiguration"]["toolUseId"] == "tu-42"

    # toolResult content is JSON-encoded and references the timezone.
    tr_payload = _event_payload(tool_events[1])
    body = json.loads(tr_payload["content"])
    assert body.get("timezone") == "UTC"

    # All three events share one fresh content name distinct from the
    # opener audio content name.
    content_names = {
        _event_payload(e).get("contentName") for e in tool_events
    }
    assert len(content_names) == 1
    assert content_names != {"c-1"}


# ---------------------------------------------------------------------------
# 5. Close idempotency and terminator order
# ---------------------------------------------------------------------------


def test_close_is_idempotent_and_sends_session_terminators():
    rpc = FakeRpc()
    session = _make_session(rpc)

    async def scenario() -> None:
        await session.open()
        await session.close()
        await session.close()  # second call must be a no-op
        await session.close()

    _run(scenario())

    kinds = [_event_kind(e) for e in rpc.sent]
    # Opener: sessionStart, promptStart, contentStart.
    # Terminators: contentEnd, promptEnd, sessionEnd.
    assert kinds == [
        "sessionStart",
        "promptStart",
        "contentStart",
        "contentEnd",
        "promptEnd",
        "sessionEnd",
    ]
    assert rpc.closed_input is True


def test_close_without_open_does_not_raise_and_is_a_noop():
    rpc = FakeRpc()
    session = _make_session(rpc)

    _run(session.close())
    assert rpc.sent == []


# ---------------------------------------------------------------------------
# 6. send_audio after close
# ---------------------------------------------------------------------------


def test_send_audio_after_close_raises_runtime_error():
    rpc = FakeRpc()
    session = _make_session(rpc)

    async def scenario() -> None:
        await session.open()
        await session.close()
        with pytest.raises(RuntimeError) as excinfo:
            await session.send_audio(b"\x00")
        assert "session closed" in str(excinfo.value)

    _run(scenario())


# ---------------------------------------------------------------------------
# 7. Concurrent writes are serialised
# ---------------------------------------------------------------------------


def test_concurrent_send_audio_and_send_tool_result_serialize():
    """The write lock must keep the contentStart/toolResult/contentEnd
    triple emitted by ``send_tool_result`` contiguous on the wire even
    when concurrent ``send_audio`` calls race with it.
    """
    rpc = FakeRpc()
    session = _make_session(rpc)

    async def scenario() -> None:
        await session.open()

        async def audio_burst() -> None:
            for i in range(20):
                await session.send_audio(bytes([i % 256]))

        async def tool_results() -> None:
            for i in range(5):
                await session.send_tool_result(
                    f"tu-{i}", {"ok": True, "i": i}
                )

        await asyncio.gather(audio_burst(), tool_results())

    _run(scenario())

    # Every recorded entry must be a complete event dict.
    for evt in rpc.sent:
        assert isinstance(evt, dict)
        assert "event" in evt and isinstance(evt["event"], dict)

    # Walk the recorded events and check that every TOOL contentStart is
    # followed immediately by toolResult and then contentEnd, with no
    # audioInput or other event slipping in between.
    state = "idle"  # "idle" | "after_tool_start" | "after_tool_result"
    open_tool_content: Optional[str] = None
    triples = 0
    for evt in rpc.sent:
        kind = _event_kind(evt)
        payload = _event_payload(evt)
        if state == "idle":
            if kind == "contentStart" and payload.get("type") == "TOOL":
                state = "after_tool_start"
                open_tool_content = payload["contentName"]
        elif state == "after_tool_start":
            assert kind == "toolResult", (
                f"expected toolResult to immediately follow TOOL contentStart, "
                f"got {kind!r}"
            )
            assert payload["contentName"] == open_tool_content
            state = "after_tool_result"
        elif state == "after_tool_result":
            assert kind == "contentEnd", (
                f"expected contentEnd to immediately follow toolResult, got {kind!r}"
            )
            assert payload["contentName"] == open_tool_content
            triples += 1
            state = "idle"
            open_tool_content = None

    assert state == "idle", "tool-result triple was left open"
    assert triples == 5, f"expected 5 complete tool-result triples, saw {triples}"

    # And we should still see all 20 audio frames somewhere in the log.
    audio_inputs = [e for e in rpc.sent if _event_kind(e) == "audioInput"]
    assert len(audio_inputs) == 20


def test_open_with_system_prompt_emits_text_triple_between_promptstart_and_audio():
    rpc = FakeRpc()
    session = _make_session(rpc, system_prompt="be brief")

    _run(session.open())

    kinds = [_event_kind(e) for e in rpc.sent]
    # Expect: sessionStart, promptStart, contentStart(TEXT/SYSTEM),
    # textInput, contentEnd, contentStart(AUDIO).
    assert kinds == [
        "sessionStart",
        "promptStart",
        "contentStart",
        "textInput",
        "contentEnd",
        "contentStart",
    ]
    sys_content_start = _event_payload(rpc.sent[2])
    assert sys_content_start["type"] == "TEXT"
    assert sys_content_start["role"] == "SYSTEM"
    text_input = _event_payload(rpc.sent[3])
    assert text_input["content"] == "be brief"
    assert text_input["promptName"] == "p-fixed"
    assert text_input["contentName"] == sys_content_start["contentName"]
    audio_start = _event_payload(rpc.sent[5])
    assert audio_start["type"] == "AUDIO"
    assert audio_start["role"] == "USER"


def test_open_without_system_prompt_skips_text_triple():
    rpc = FakeRpc()
    session = _make_session(rpc, system_prompt=None)

    _run(session.open())

    kinds = [_event_kind(e) for e in rpc.sent]
    assert kinds == ["sessionStart", "promptStart", "contentStart"]
