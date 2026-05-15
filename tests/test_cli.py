"""Integration tests for :mod:`nova_sonic_demo.cli`.

The real CLI wires together PortAudio, boto3, and the Bedrock
bidirectional stream. None of those are touched here. Instead we inject
fakes for every collaborator and drive :func:`nova_sonic_demo.cli.run`
directly with ``asyncio.run``:

* ``FakeSd`` -- mirrors the shim from ``test_audio.py`` so the
  capturer/player can ``start()``/``stop()`` without a real device.
* ``FakeCapturer`` / ``FakePlayer`` -- track ``start``/``stop``/
  ``enqueue`` calls and also append into a shared ``call_log`` so we can
  assert shutdown ordering.
* ``FakeSession`` -- accepts an optional :class:`BedrockOpenError` to
  raise from ``open()``, otherwise yields a configurable list of events
  from ``stream_events`` and records ``close()`` invocations.

Each test monkeypatches ``resolve_region`` and
``assert_credentials_resolvable`` from ``nova_sonic_demo.cli`` so no real
boto3 call is made.
"""

from __future__ import annotations

import asyncio
import io
import time
from typing import Optional

import pytest

from nova_sonic_demo import cli
from nova_sonic_demo.config import (
    BedrockOpenError,
    MissingCredentialsError,
)
from nova_sonic_demo.events import AudioOutEvent, TranscriptEvent


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSd:
    """Minimal ``sounddevice`` replacement used by ``probe_audio_devices``.

    The capturer/player factories used in these tests don't actually
    call ``RawInputStream``/``RawOutputStream`` (we use ``FakeCapturer``
    and ``FakePlayer`` instead), so we only need ``query_devices`` here.
    """

    def __init__(self, devices=None) -> None:
        self.devices = (
            devices
            if devices is not None
            else [
                {"max_input_channels": 1, "max_output_channels": 0, "name": "mic"},
                {"max_input_channels": 0, "max_output_channels": 2, "name": "spk"},
            ]
        )

    def query_devices(self):
        return list(self.devices)


class FakeCapturer:
    def __init__(self, *, on_frame, call_log: list, sd=None) -> None:
        self.on_frame = on_frame
        self.sd = sd
        self.call_log = call_log
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        self.call_log.append(("capturer", "start"))

    async def stop(self) -> None:
        self.stop_calls += 1
        self.call_log.append(("capturer", "stop"))


class FakePlayer:
    def __init__(self, *, call_log: list, sd=None) -> None:
        self.sd = sd
        self.call_log = call_log
        self.start_calls = 0
        self.stop_calls = 0
        self.enqueued: list[bytes] = []

    async def start(self) -> None:
        self.start_calls += 1
        self.call_log.append(("player", "start"))

    async def enqueue(self, pcm: bytes) -> None:
        self.enqueued.append(pcm)

    async def stop(self) -> None:
        self.stop_calls += 1
        self.call_log.append(("player", "stop"))


class FakeSession:
    """Stand-in for :class:`SonicSession`.

    ``open_error`` -- if set, ``open()`` raises it.
    ``events`` -- a list of :class:`AudioOutEvent` or
    :class:`TranscriptEvent` to yield from ``stream_events``.
    ``hold_open`` -- when True, ``stream_events`` waits on an internal
    asyncio Event until ``release()`` is called. Used by the
    Ctrl+C-equivalent test.
    """

    def __init__(
        self,
        *,
        call_log: list,
        events: Optional[list] = None,
        open_error: Optional[BaseException] = None,
        hold_open: bool = False,
    ) -> None:
        self.call_log = call_log
        self.events = list(events or [])
        self.open_error = open_error
        self.hold_open = hold_open
        self._release = asyncio.Event()
        self.open_calls = 0
        self.close_calls = 0
        self.audio_sent: list[bytes] = []

    async def open(self) -> None:
        self.open_calls += 1
        if self.open_error is not None:
            raise self.open_error

    async def send_audio(self, pcm: bytes) -> None:
        self.audio_sent.append(pcm)

    async def stream_events(self):
        for ev in self.events:
            await asyncio.sleep(0)
            yield ev
        if self.hold_open:
            # Wait for an external signal before returning so the test
            # can simulate a long-running session that ends only when
            # the CLI requests a stop.
            await self._release.wait()

    def release(self) -> None:
        self._release.set()

    async def close(self) -> None:
        self.close_calls += 1
        self.call_log.append(("session", "close"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_env(monkeypatch, *, region: str = "us-east-1", credentials_ok: bool = True):
    """Stub region resolution and credential assertion."""

    monkeypatch.setattr(cli, "resolve_region", lambda: region)

    def _assert():
        if not credentials_ok:
            raise MissingCredentialsError("missing")

    monkeypatch.setattr(cli, "assert_credentials_resolvable", _assert)


def _make_factories(*, session: FakeSession, capturer: FakeCapturer, player: FakePlayer):
    def session_factory(region, registry, logger, dispatcher, *, client_factory=None):
        return session

    def capturer_factory(*, on_frame, sd=None):
        # The on_frame supplied by the CLI is the session's send_audio.
        # FakeCapturer never calls it; we just record the wiring.
        capturer.on_frame = on_frame
        capturer.sd = sd
        return capturer

    def player_factory(*, sd=None, prebuffer_ms=0):
        player.sd = sd
        player.prebuffer_ms = prebuffer_ms
        return player

    return session_factory, capturer_factory, player_factory


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Missing input device -> exit 3
# ---------------------------------------------------------------------------


def test_run_returns_3_when_input_device_missing(monkeypatch):
    sd = FakeSd(
        devices=[{"max_input_channels": 0, "max_output_channels": 2, "name": "spk"}]
    )
    stderr = io.StringIO()
    _patch_env(monkeypatch)

    rc = _run(cli.run([], sd=sd, stderr=stderr))

    assert rc == 3
    assert "Missing input device" in stderr.getvalue()


# ---------------------------------------------------------------------------
# 2. Missing output device -> exit 3
# ---------------------------------------------------------------------------


def test_run_returns_3_when_output_device_missing(monkeypatch):
    sd = FakeSd(
        devices=[{"max_input_channels": 1, "max_output_channels": 0, "name": "mic"}]
    )
    stderr = io.StringIO()
    _patch_env(monkeypatch)

    rc = _run(cli.run([], sd=sd, stderr=stderr))

    assert rc == 3
    assert "Missing output device" in stderr.getvalue()


# ---------------------------------------------------------------------------
# 3. Unsupported region -> exit 2
# ---------------------------------------------------------------------------


def test_run_returns_2_when_region_unsupported(monkeypatch):
    sd = FakeSd()
    stderr = io.StringIO()
    _patch_env(monkeypatch, region="eu-central-1")

    rc = _run(cli.run([], sd=sd, stderr=stderr))

    assert rc == 2
    assert "eu-central-1" in stderr.getvalue()
    assert "Nova Sonic" in stderr.getvalue()


# ---------------------------------------------------------------------------
# 4. Missing credentials -> exit 4
# ---------------------------------------------------------------------------


def test_run_returns_4_when_credentials_missing(monkeypatch):
    sd = FakeSd()
    stderr = io.StringIO()
    _patch_env(monkeypatch, credentials_ok=False)

    rc = _run(cli.run([], sd=sd, stderr=stderr))

    assert rc == 4
    assert "AWS credentials missing or invalid" in stderr.getvalue()


# ---------------------------------------------------------------------------
# 5. session.open failure -> exit 5
# ---------------------------------------------------------------------------


def test_run_returns_5_when_session_open_fails(monkeypatch):
    sd = FakeSd()
    stderr = io.StringIO()
    _patch_env(monkeypatch)

    call_log: list = []
    session = FakeSession(
        call_log=call_log,
        open_error=BedrockOpenError("auth", "denied"),
    )
    capturer = FakeCapturer(on_frame=lambda *_: None, call_log=call_log)
    player = FakePlayer(call_log=call_log)
    s_fac, c_fac, p_fac = _make_factories(
        session=session, capturer=capturer, player=player
    )

    rc = _run(
        cli.run(
            [],
            sd=sd,
            stderr=stderr,
            sonic_session_factory=s_fac,
            capturer_factory=c_fac,
            player_factory=p_fac,
        )
    )

    assert rc == 5
    msg = stderr.getvalue()
    assert "auth" in msg
    assert "denied" in msg
    # Capturer/player should not have been started since open failed first.
    assert capturer.start_calls == 0
    assert player.start_calls == 0


# ---------------------------------------------------------------------------
# 6. Happy path -> exit 0, banner + LISTENING + ASSISTANT lines printed
# ---------------------------------------------------------------------------


def test_run_happy_path_returns_0_and_prints_banner_and_listening(
    monkeypatch, capsys
):
    sd = FakeSd()
    stderr = io.StringIO()
    _patch_env(monkeypatch)

    call_log: list = []
    session = FakeSession(
        call_log=call_log,
        events=[
            TranscriptEvent(role="ASSISTANT", text="hello there", is_final=True),
        ],
    )
    capturer = FakeCapturer(on_frame=lambda *_: None, call_log=call_log)
    player = FakePlayer(call_log=call_log)
    s_fac, c_fac, p_fac = _make_factories(
        session=session, capturer=capturer, player=player
    )

    rc = _run(
        cli.run(
            ["--no-vad"],
            sd=sd,
            stderr=stderr,
            sonic_session_factory=s_fac,
            capturer_factory=c_fac,
            player_factory=p_fac,
        )
    )

    out = capsys.readouterr().out
    assert rc == 0
    # Banner contains model id and region.
    assert "amazon.nova-2-sonic-v1:0" in out
    assert "us-east-1" in out
    # Listening line.
    assert "LISTENING:" in out
    # Assistant line.
    assert "ASSISTANT: hello there" in out
    # Each lifecycle hook ran exactly once.
    assert capturer.start_calls == 1
    assert capturer.stop_calls == 1
    assert player.start_calls == 1
    assert player.stop_calls == 1
    assert session.close_calls == 1


# ---------------------------------------------------------------------------
# 7. AudioOutEvent routes to player.enqueue
# ---------------------------------------------------------------------------


def test_run_routes_audio_output_event_to_player_enqueue(monkeypatch):
    sd = FakeSd()
    stderr = io.StringIO()
    _patch_env(monkeypatch)

    call_log: list = []
    payload = b"\x00\x01\x02\x03"
    session = FakeSession(
        call_log=call_log,
        events=[AudioOutEvent(pcm=payload)],
    )
    capturer = FakeCapturer(on_frame=lambda *_: None, call_log=call_log)
    player = FakePlayer(call_log=call_log)
    s_fac, c_fac, p_fac = _make_factories(
        session=session, capturer=capturer, player=player
    )

    rc = _run(
        cli.run(
            ["--no-vad"],
            sd=sd,
            stderr=stderr,
            sonic_session_factory=s_fac,
            capturer_factory=c_fac,
            player_factory=p_fac,
        )
    )

    assert rc == 0
    assert player.enqueued == [payload]


# ---------------------------------------------------------------------------
# 8. Long-running session can be stopped within the shutdown deadline
# ---------------------------------------------------------------------------


def test_run_keyboard_interrupt_path_completes_within_5_seconds(monkeypatch):
    """Simulate a long-running session and request a clean stop.

    Using a real SIGINT inside ``asyncio.run`` is platform-specific and
    can race the test runner. Instead we drive the same code path by
    setting the SonicSession's release event from a separate task; the
    event-loop task finishes naturally and the CLI advances to shutdown.
    """

    sd = FakeSd()
    stderr = io.StringIO()
    _patch_env(monkeypatch)

    call_log: list = []
    session = FakeSession(call_log=call_log, hold_open=True)
    capturer = FakeCapturer(on_frame=lambda *_: None, call_log=call_log)
    player = FakePlayer(call_log=call_log)
    s_fac, c_fac, p_fac = _make_factories(
        session=session, capturer=capturer, player=player
    )

    async def driver() -> int:
        async def releaser() -> None:
            await asyncio.sleep(0.05)
            session.release()

        rc, _ = await asyncio.gather(
            cli.run(
                ["--no-vad"],
                sd=sd,
                stderr=stderr,
                sonic_session_factory=s_fac,
                capturer_factory=c_fac,
                player_factory=p_fac,
            ),
            releaser(),
        )
        return rc

    start = time.monotonic()
    rc = _run(driver())
    elapsed = time.monotonic() - start

    assert rc == 0
    # The whole thing should be quick -- well within the 5 s shutdown
    # deadline. Allow generous slack for slow CI machines.
    assert elapsed < 2.0, f"shutdown took {elapsed:.2f}s, expected < 2.0s"
    assert session.close_calls == 1
    assert capturer.stop_calls == 1
    assert player.stop_calls == 1


# ---------------------------------------------------------------------------
# 9. Shutdown order: capturer.stop -> player.stop -> session.close
# ---------------------------------------------------------------------------


def test_run_shutdown_calls_capturer_player_session_close_in_order(monkeypatch):
    sd = FakeSd()
    stderr = io.StringIO()
    _patch_env(monkeypatch)

    call_log: list = []
    session = FakeSession(call_log=call_log, events=[])
    capturer = FakeCapturer(on_frame=lambda *_: None, call_log=call_log)
    player = FakePlayer(call_log=call_log)
    s_fac, c_fac, p_fac = _make_factories(
        session=session, capturer=capturer, player=player
    )

    rc = _run(
        cli.run(
            ["--no-vad"],
            sd=sd,
            stderr=stderr,
            sonic_session_factory=s_fac,
            capturer_factory=c_fac,
            player_factory=p_fac,
        )
    )

    assert rc == 0
    # Filter shutdown events out of the full log: starts come first,
    # then stops in shutdown order.
    stop_events = [e for e in call_log if e[1] in ("stop", "close")]
    assert stop_events == [
        ("capturer", "stop"),
        ("player", "stop"),
        ("session", "close"),
    ]


# ---------------------------------------------------------------------------
# 10. main() invokes run and exits cleanly on KeyboardInterrupt
# ---------------------------------------------------------------------------


def test_main_exits_with_code_from_run(monkeypatch):
    monkeypatch.setattr("sys.argv", ["nova_sonic_demo"])

    async def fake_run(args):
        assert args == []
        return 0

    monkeypatch.setattr(cli, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 0


def test_main_maps_keyboard_interrupt_to_exit_zero(monkeypatch):
    monkeypatch.setattr("sys.argv", ["nova_sonic_demo"])

    async def fake_run(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 0
