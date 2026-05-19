"""Unit tests for nova_sonic_demo.web.agentcore_session_manager.AgentCoreSessionManager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_sonic_demo.web.agentcore_session_manager import AgentCoreSessionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_manager(
    *,
    client_factory=None,
    agent_id="test-agent-id",
    agent_alias_id="test-alias-id",
    region="us-east-1",
):
    """Create an AgentCoreSessionManager with mock send callables."""
    send_text = AsyncMock()
    send_bytes = AsyncMock()
    mgr = AgentCoreSessionManager(
        send_text,
        send_bytes,
        agent_id=agent_id,
        agent_alias_id=agent_alias_id,
        region=region,
        client_factory=client_factory,
    )
    return mgr, send_text, send_bytes


def _mock_client_factory(invoke_response=None, invoke_side_effect=None):
    """Return a client factory that produces a mock bedrock-agent-runtime client."""
    mock_client = MagicMock()
    if invoke_side_effect:
        mock_client.invoke_agent = MagicMock(side_effect=invoke_side_effect)
    else:
        if invoke_response is None:
            invoke_response = {"completion": MagicMock()}
        mock_client.invoke_agent = MagicMock(return_value=invoke_response)

    def factory(region):
        return mock_client

    return factory, mock_client


def _mock_client_factory_with_stream(events):
    """Return a client factory whose response stream yields the given events."""
    mock_stream = MagicMock()
    mock_stream.__iter__ = MagicMock(return_value=iter(events))
    mock_stream.close = MagicMock()

    mock_client = MagicMock()
    mock_client.invoke_agent = MagicMock(return_value={"completion": mock_stream})

    def factory(region):
        return mock_client

    return factory, mock_client, mock_stream


# ---------------------------------------------------------------------------
# State machine tests
# ---------------------------------------------------------------------------


class TestAgentCoreSessionManagerState:
    """Test state machine transitions."""

    def test_initial_state_is_ready(self):
        mgr, _, _ = _make_manager()
        assert mgr.state == "ready"

    def test_start_transitions_to_active(self):
        factory, mock_client = _mock_client_factory()
        mgr, send_text, _ = _make_manager(client_factory=factory)

        _run(mgr.start())

        assert mgr.state == "active"
        # Should have sent connecting then active status messages
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any('"connecting"' in t for t in texts)
        assert any('"active"' in t for t in texts)

    def test_start_client_creation_error_transitions_to_error(self):
        def bad_factory(region):
            raise RuntimeError("Cannot create client")

        mgr, send_text, _ = _make_manager(client_factory=bad_factory)

        _run(mgr.start())

        assert mgr.state == "error"
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any("Cannot create client" in t for t in texts)

    def test_start_invoke_agent_error_transitions_to_error(self):
        factory, mock_client = _mock_client_factory(
            invoke_side_effect=Exception("AccessDeniedException: bad token")
        )
        mgr, send_text, _ = _make_manager(client_factory=factory)

        _run(mgr.start())

        assert mgr.state == "error"
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any("AgentCore" in t for t in texts)

    def test_start_timeout_error_transitions_to_error(self):
        factory, mock_client = _mock_client_factory(
            invoke_side_effect=Exception("ConnectTimeoutError: timed out")
        )
        mgr, send_text, _ = _make_manager(client_factory=factory)

        _run(mgr.start())

        assert mgr.state == "error"
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any("timeout" in t.lower() for t in texts)

    def test_start_network_error_transitions_to_error(self):
        factory, mock_client = _mock_client_factory(
            invoke_side_effect=Exception("ConnectionError: network unreachable")
        )
        mgr, send_text, _ = _make_manager(client_factory=factory)

        _run(mgr.start())

        assert mgr.state == "error"

    def test_stop_transitions_to_ready(self):
        factory, mock_client = _mock_client_factory()
        mgr, send_text, _ = _make_manager(client_factory=factory)

        async def _start_and_stop():
            await mgr.start()
            await mgr.stop()

        _run(_start_and_stop())
        assert mgr.state == "ready"

    def test_stop_from_ready_is_noop(self):
        mgr, send_text, _ = _make_manager()
        _run(mgr.stop())
        assert mgr.state == "ready"
        # No messages sent since we're already ready
        send_text.assert_not_called()

    def test_start_ignored_when_active(self):
        factory, mock_client = _mock_client_factory()
        mgr, send_text, _ = _make_manager(client_factory=factory)

        async def _start_twice():
            await mgr.start()
            send_text.reset_mock()
            # Try starting again while active — should be ignored
            await mgr.start()

        _run(_start_twice())
        send_text.assert_not_called()

    def test_start_from_error_state_retries(self):
        """After an error, start() should retry and can succeed."""
        call_count = [0]

        def factory_with_retry(region):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: factory succeeds but invoke_agent fails
                client = MagicMock()
                client.invoke_agent = MagicMock(
                    side_effect=Exception("Temporary failure")
                )
                return client
            else:
                # Second call: succeeds
                client = MagicMock()
                client.invoke_agent = MagicMock(
                    return_value={"completion": MagicMock()}
                )
                return client

        mgr, send_text, _ = _make_manager(client_factory=factory_with_retry)

        async def _test():
            await mgr.start()
            assert mgr.state == "error"
            # Retry from error state
            await mgr.start()
            assert mgr.state == "active"

        _run(_test())

    def test_stop_from_error_transitions_to_ready(self):
        factory, mock_client = _mock_client_factory(
            invoke_side_effect=Exception("Some error")
        )
        mgr, send_text, _ = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            assert mgr.state == "error"
            await mgr.stop()
            assert mgr.state == "ready"

        _run(_test())


# ---------------------------------------------------------------------------
# Audio handling tests
# ---------------------------------------------------------------------------


class TestAgentCoreSessionManagerAudio:
    """Test audio forwarding behavior."""

    def test_handle_audio_forwards_when_active(self):
        mock_stream = MagicMock()
        mock_stream.send_audio_event = MagicMock()

        factory, mock_client = _mock_client_factory(
            invoke_response={"completion": mock_stream}
        )
        mgr, _, _ = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            pcm = b"\x00\x01" * 160  # valid PCM: 320 bytes, even
            await mgr.handle_audio(pcm)

        _run(_test())
        mock_stream.send_audio_event.assert_called_once_with(audio=b"\x00\x01" * 160)

    def test_handle_audio_ignored_when_not_active(self):
        mgr, _, _ = _make_manager()
        # State is 'ready'
        _run(mgr.handle_audio(b"\x00\x01" * 160))
        # No error — just silently ignored

    def test_handle_audio_ignored_when_error(self):
        factory, mock_client = _mock_client_factory(
            invoke_side_effect=Exception("fail")
        )
        mgr, _, _ = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            assert mgr.state == "error"
            await mgr.handle_audio(b"\x00\x01" * 160)

        _run(_test())
        # No crash, audio silently dropped

    def test_handle_audio_rejects_empty_bytes(self):
        mock_stream = MagicMock()
        mock_stream.send_audio_event = MagicMock()

        factory, mock_client = _mock_client_factory(
            invoke_response={"completion": mock_stream}
        )
        mgr, _, _ = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            await mgr.handle_audio(b"")

        _run(_test())
        mock_stream.send_audio_event.assert_not_called()

    def test_handle_audio_rejects_odd_length(self):
        mock_stream = MagicMock()
        mock_stream.send_audio_event = MagicMock()

        factory, mock_client = _mock_client_factory(
            invoke_response={"completion": mock_stream}
        )
        mgr, _, _ = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            await mgr.handle_audio(b"\x00\x01\x02")  # 3 bytes, odd

        _run(_test())
        mock_stream.send_audio_event.assert_not_called()


# ---------------------------------------------------------------------------
# Event loop tests
# ---------------------------------------------------------------------------


class TestAgentCoreSessionManagerEventLoop:
    """Test event routing from AgentCore stream to WebSocket."""

    def test_audio_event_sent_as_binary(self):
        audio_data = b"\x01\x02" * 100
        events = [{"chunk": {"bytes": audio_data}}]
        factory, mock_client, mock_stream = _mock_client_factory_with_stream(events)
        mgr, send_text, send_bytes = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            await mgr.run_event_loop()

        _run(_test())
        send_bytes.assert_awaited_once_with(audio_data)

    def test_transcript_event_sent_as_json(self):
        events = [{"transcript": {"role": "USER", "text": "hello"}}]
        factory, mock_client, mock_stream = _mock_client_factory_with_stream(events)
        mgr, send_text, send_bytes = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            send_text.reset_mock()
            await mgr.run_event_loop()

        _run(_test())
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any('"transcript"' in t and '"USER"' in t and "hello" in t for t in texts)

    def test_tool_call_event_sent_as_json(self):
        events = [{"tool_call": {"name": "get_weather", "arguments": {"city": "Tokyo"}}}]
        factory, mock_client, mock_stream = _mock_client_factory_with_stream(events)
        mgr, send_text, send_bytes = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            send_text.reset_mock()
            await mgr.run_event_loop()

        _run(_test())
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any('"tool_call"' in t and "get_weather" in t for t in texts)

    def test_tool_result_event_sent_as_json(self):
        events = [{"tool_result": {"name": "get_weather", "result": {"temp": 20}}}]
        factory, mock_client, mock_stream = _mock_client_factory_with_stream(events)
        mgr, send_text, send_bytes = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            send_text.reset_mock()
            await mgr.run_event_loop()

        _run(_test())
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any('"tool_result"' in t and "get_weather" in t for t in texts)

    def test_run_event_loop_noop_without_stream(self):
        mgr, send_text, send_bytes = _make_manager()
        # No session started — should return immediately
        _run(mgr.run_event_loop())
        send_text.assert_not_called()
        send_bytes.assert_not_called()

    def test_stream_error_transitions_to_error(self):
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(
            side_effect=RuntimeError("Stream interrupted")
        )
        mock_stream.close = MagicMock()

        factory, mock_client = _mock_client_factory(
            invoke_response={"completion": mock_stream}
        )
        mgr, send_text, _ = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            assert mgr.state == "active"
            await mgr.run_event_loop()

        _run(_test())
        assert mgr.state == "error"


# ---------------------------------------------------------------------------
# Stop / shutdown tests
# ---------------------------------------------------------------------------


class TestAgentCoreSessionManagerStop:
    """Test graceful shutdown behavior."""

    def test_stop_closes_stream(self):
        mock_stream = MagicMock()
        mock_stream.close = MagicMock()

        factory, mock_client = _mock_client_factory(
            invoke_response={"completion": mock_stream}
        )
        mgr, _, _ = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            await mgr.stop()

        _run(_test())
        mock_stream.close.assert_called_once()

    def test_stop_resets_references(self):
        factory, mock_client = _mock_client_factory()
        mgr, _, _ = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            assert mgr._client is not None
            assert mgr._response_stream is not None
            await mgr.stop()
            assert mgr._client is None
            assert mgr._response_stream is None
            assert mgr._session_id is None

        _run(_test())

    def test_stop_allows_restart(self):
        """After stop(), the manager can be started again."""
        factory, mock_client = _mock_client_factory()
        mgr, _, _ = _make_manager(client_factory=factory)

        async def _test():
            await mgr.start()
            assert mgr.state == "active"
            await mgr.stop()
            assert mgr.state == "ready"
            await mgr.start()
            assert mgr.state == "active"

        _run(_test())
