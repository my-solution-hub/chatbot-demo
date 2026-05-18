"""Unit tests for nova_sonic_demo.web.session_manager.SessionManager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_sonic_demo.config import (
    BedrockOpenError,
    MissingCredentialsError,
    UnsupportedRegionError,
)
from nova_sonic_demo.events import AudioOutEvent, TranscriptEvent
from nova_sonic_demo.web.session_manager import SessionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_manager(
    *,
    session_factory=None,
    registry_factory=None,
    dispatcher_factory=None,
):
    """Create a SessionManager with mock send callables."""
    send_text = AsyncMock()
    send_bytes = AsyncMock()
    mgr = SessionManager(
        send_text,
        send_bytes,
        session_factory=session_factory,
        registry_factory=registry_factory,
        dispatcher_factory=dispatcher_factory,
    )
    return mgr, send_text, send_bytes


def _fake_session_factory(open_side_effect=None):
    """Return a factory that produces a mock SonicSession."""
    mock_session = AsyncMock()
    mock_session.open = AsyncMock(side_effect=open_side_effect)
    mock_session.send_audio = AsyncMock()
    mock_session.close = AsyncMock()
    mock_session.stream_events = MagicMock(return_value=_empty_async_iter())

    def factory(region, registry, logger, dispatcher):
        return mock_session

    return factory, mock_session


async def _empty_async_iter():
    return
    yield  # noqa: make it an async generator


async def _events_async_iter(*events):
    for event in events:
        yield event


async def _start_manager(mgr):
    """Helper to start a manager with mocked AWS calls."""
    with patch("nova_sonic_demo.web.session_manager.assert_credentials_resolvable"):
        with patch("nova_sonic_demo.web.session_manager.resolve_region", return_value="us-east-1"):
            with patch("nova_sonic_demo.web.session_manager.validate_region"):
                await mgr.start()


# ---------------------------------------------------------------------------
# State machine tests
# ---------------------------------------------------------------------------


class TestSessionManagerState:
    """Test state machine transitions."""

    def test_initial_state_is_ready(self):
        mgr, _, _ = _make_manager()
        assert mgr.state == "ready"

    def test_start_transitions_to_active(self):
        factory, mock_session = _fake_session_factory()
        mgr, send_text, _ = _make_manager(
            session_factory=factory,
            registry_factory=MagicMock(return_value=MagicMock()),
            dispatcher_factory=MagicMock(return_value=MagicMock()),
        )

        _run(_start_manager(mgr))

        assert mgr.state == "active"
        # Should have sent connecting then active status messages
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any('"connecting"' in t for t in texts)
        assert any('"active"' in t for t in texts)

    def test_start_credentials_error_transitions_to_error(self):
        mgr, send_text, _ = _make_manager()

        with patch(
            "nova_sonic_demo.web.session_manager.assert_credentials_resolvable",
            side_effect=MissingCredentialsError("no creds"),
        ):
            _run(mgr.start())

        assert mgr.state == "error"
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any("credentials" in t.lower() for t in texts)

    def test_start_region_error_transitions_to_error(self):
        mgr, send_text, _ = _make_manager()

        with patch("nova_sonic_demo.web.session_manager.assert_credentials_resolvable"):
            with patch("nova_sonic_demo.web.session_manager.resolve_region", return_value="eu-west-1"):
                with patch(
                    "nova_sonic_demo.web.session_manager.validate_region",
                    side_effect=UnsupportedRegionError("Region eu-west-1 does not support Nova Sonic v2"),
                ):
                    _run(mgr.start())

        assert mgr.state == "error"
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any("eu-west-1" in t for t in texts)

    def test_start_bedrock_open_error_transitions_to_error(self):
        factory, mock_session = _fake_session_factory(
            open_side_effect=BedrockOpenError("auth", "bad token")
        )
        mgr, send_text, _ = _make_manager(
            session_factory=factory,
            registry_factory=MagicMock(return_value=MagicMock()),
            dispatcher_factory=MagicMock(return_value=MagicMock()),
        )

        _run(_start_manager(mgr))

        assert mgr.state == "error"
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any("Bedrock" in t for t in texts)

    def test_stop_transitions_to_closed(self):
        factory, mock_session = _fake_session_factory()
        mgr, send_text, _ = _make_manager(
            session_factory=factory,
            registry_factory=MagicMock(return_value=MagicMock()),
            dispatcher_factory=MagicMock(return_value=MagicMock()),
        )

        async def _start_and_stop():
            await _start_manager(mgr)
            await mgr.stop()

        _run(_start_and_stop())
        assert mgr.state == "closed"
        mock_session.close.assert_awaited_once()

    def test_stop_from_ready_is_noop(self):
        mgr, send_text, _ = _make_manager()
        _run(mgr.stop())
        assert mgr.state == "ready"

    def test_start_ignored_when_not_ready(self):
        factory, mock_session = _fake_session_factory()
        mgr, send_text, _ = _make_manager(
            session_factory=factory,
            registry_factory=MagicMock(return_value=MagicMock()),
            dispatcher_factory=MagicMock(return_value=MagicMock()),
        )

        async def _start_twice():
            await _start_manager(mgr)
            send_text.reset_mock()
            # Try starting again while active — should be ignored
            await mgr.start()

        _run(_start_twice())
        send_text.assert_not_called()


# ---------------------------------------------------------------------------
# Audio handling tests
# ---------------------------------------------------------------------------


class TestSessionManagerAudio:
    """Test audio forwarding behavior."""

    def test_handle_audio_forwards_when_active(self):
        factory, mock_session = _fake_session_factory()
        mgr, _, _ = _make_manager(
            session_factory=factory,
            registry_factory=MagicMock(return_value=MagicMock()),
            dispatcher_factory=MagicMock(return_value=MagicMock()),
        )

        async def _test():
            await _start_manager(mgr)
            pcm = b"\x00\x01" * 160  # valid PCM: 320 bytes, even
            await mgr.handle_audio(pcm)

        _run(_test())
        mock_session.send_audio.assert_awaited_once_with(b"\x00\x01" * 160)

    def test_handle_audio_ignored_when_not_active(self):
        mgr, _, _ = _make_manager()
        # State is 'ready'
        _run(mgr.handle_audio(b"\x00\x01" * 160))
        # No session, no error — just silently ignored

    def test_handle_audio_rejects_empty_bytes(self):
        factory, mock_session = _fake_session_factory()
        mgr, _, _ = _make_manager(
            session_factory=factory,
            registry_factory=MagicMock(return_value=MagicMock()),
            dispatcher_factory=MagicMock(return_value=MagicMock()),
        )

        async def _test():
            await _start_manager(mgr)
            await mgr.handle_audio(b"")

        _run(_test())
        mock_session.send_audio.assert_not_awaited()

    def test_handle_audio_rejects_odd_length(self):
        factory, mock_session = _fake_session_factory()
        mgr, _, _ = _make_manager(
            session_factory=factory,
            registry_factory=MagicMock(return_value=MagicMock()),
            dispatcher_factory=MagicMock(return_value=MagicMock()),
        )

        async def _test():
            await _start_manager(mgr)
            await mgr.handle_audio(b"\x00\x01\x02")  # 3 bytes, odd

        _run(_test())
        mock_session.send_audio.assert_not_awaited()


# ---------------------------------------------------------------------------
# Event loop tests
# ---------------------------------------------------------------------------


class TestSessionManagerEventLoop:
    """Test event routing from SonicSession to WebSocket."""

    def test_audio_out_event_sent_as_binary(self):
        factory, mock_session = _fake_session_factory()
        mgr, send_text, send_bytes = _make_manager(
            session_factory=factory,
            registry_factory=MagicMock(return_value=MagicMock()),
            dispatcher_factory=MagicMock(return_value=MagicMock()),
        )

        pcm_data = b"\x01\x02" * 100

        async def _test():
            await _start_manager(mgr)
            mock_session.stream_events = MagicMock(
                return_value=_events_async_iter(AudioOutEvent(pcm=pcm_data))
            )
            await mgr.run_event_loop()

        _run(_test())
        send_bytes.assert_awaited_once_with(pcm_data)

    def test_transcript_event_sent_as_json(self):
        factory, mock_session = _fake_session_factory()
        mgr, send_text, send_bytes = _make_manager(
            session_factory=factory,
            registry_factory=MagicMock(return_value=MagicMock()),
            dispatcher_factory=MagicMock(return_value=MagicMock()),
        )

        async def _test():
            await _start_manager(mgr)
            mock_session.stream_events = MagicMock(
                return_value=_events_async_iter(
                    TranscriptEvent(role="USER", text="hello", is_final=True)
                )
            )
            send_text.reset_mock()
            await mgr.run_event_loop()

        _run(_test())
        # Find the transcript message among sent texts
        calls = send_text.call_args_list
        texts = [c[0][0] for c in calls]
        assert any('"transcript"' in t and '"USER"' in t and "hello" in t for t in texts)

    def test_run_event_loop_noop_without_session(self):
        mgr, send_text, send_bytes = _make_manager()
        # No session started — should return immediately
        _run(mgr.run_event_loop())
        send_text.assert_not_called()
        send_bytes.assert_not_called()
