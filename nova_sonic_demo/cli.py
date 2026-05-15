"""Demo_CLI: command-line entry point and lifecycle manager (task 10).

The lifecycle is documented in ``design.md`` and summarised here:

1. Probe audio devices.
2. Resolve the AWS region and validate it against ``SUPPORTED_REGIONS``.
3. Assert that AWS credentials can be resolved from the SDK chain.
4. Build the tool registry, dispatcher, and Sonic session.
5. Open the session (10-second deadline enforced internally).
6. Mark the logger session-active and emit the startup banner.
7. Start the audio capturer/player and emit the ``LISTENING:`` line.
8. Run the event loop until the user interrupts (Ctrl+C) or the session
   ends naturally.
9. Shut down within 5 seconds: stop capturer, stop player, close session,
   mark the logger closed.

Each step maps to an exit code for fatal failures (see the table in
``design.md``):

================================ =====
failure                          exit
================================ =====
MissingDeviceError                  3
UnsupportedRegionError              2
MissingCredentialsError             4
BedrockOpenError                    5
================================ =====

All collaborators are injectable so the lifecycle can be exercised in
unit tests without touching PortAudio or AWS.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from typing import Optional

from .audio import AudioCapturer, AudioPlayer, VADGate, probe_audio_devices
from .config import (
    MODEL_ID,
    PLAYER_PREBUFFER_MS,
    SHUTDOWN_DEADLINE_S,
    VAD_AGGRESSIVENESS,
    VAD_BATCH_FRAMES,
    VAD_FRAME_MS,
    VAD_HANGOVER_MS,
    VAD_PREROLL_MS,
    BedrockOpenError,
    MissingCredentialsError,
    MissingDeviceError,
    UnsupportedRegionError,
    assert_credentials_resolvable,
    resolve_region,
    validate_region,
)
from .events import AudioOutEvent, TranscriptEvent
from .logging import ConsoleLogger
from .session import SonicSession
from .tools.registry import ToolDispatcher, build_default_registry


# ---------------------------------------------------------------------------
# Default factories (injectable for tests)
# ---------------------------------------------------------------------------


DEFAULT_SYSTEM_PROMPT = (
    "You are a friendly voice assistant. Keep replies short and natural. "
    "When the user asks about the time, call the get_current_time tool. "
    "When the user asks about the weather, call the get_weather tool. "
    "After a tool returns, summarize the result in one or two sentences."
)


def _default_session_factory(
    region,
    registry,
    logger,
    dispatcher,
    *,
    client_factory=None,
):
    """Build a real :class:`SonicSession` bound to ``region``."""

    return SonicSession(
        region=region,
        registry=registry,
        logger=logger,
        dispatcher=dispatcher,
        client_factory=client_factory,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
    )


def _default_capturer_factory(*, on_frame, sd=None):
    if sd is not None:
        return AudioCapturer(on_frame, sd=sd)
    return AudioCapturer(on_frame)


def _default_player_factory(*, sd=None, prebuffer_ms: int = PLAYER_PREBUFFER_MS):
    if sd is not None:
        return AudioPlayer(sd=sd, prebuffer_ms=prebuffer_ms)
    return AudioPlayer(prebuffer_ms=prebuffer_ms)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nova_sonic_demo",
        description="End-to-end Nova Sonic v2 speech demo with two simple tools.",
    )
    parser.add_argument(
        "--no-vad",
        action="store_true",
        help="Disable local VAD gating; stream every microphone frame.",
    )
    parser.add_argument(
        "--vad-aggressiveness",
        type=int,
        choices=(0, 1, 2, 3),
        default=VAD_AGGRESSIVENESS,
        help="webrtcvad aggressiveness (0=lenient, 3=strict). Default: 2.",
    )
    parser.add_argument(
        "--vad-frame-ms",
        type=int,
        choices=(10, 20, 30),
        default=VAD_FRAME_MS,
        help="VAD analysis frame size. Must be 10, 20, or 30 ms. Default: 20.",
    )
    parser.add_argument(
        "--vad-batch-frames",
        type=int,
        default=VAD_BATCH_FRAMES,
        help=(
            "Number of VAD frames to coalesce into one Bedrock audioInput "
            "event (4 = 80 ms). Higher reduces protocol overhead."
        ),
    )
    parser.add_argument(
        "--vad-hangover-ms",
        type=int,
        default=VAD_HANGOVER_MS,
        help="Continue streaming for this long after the last voice frame.",
    )
    parser.add_argument(
        "--vad-preroll-ms",
        type=int,
        default=VAD_PREROLL_MS,
        help="Include this much pre-trigger audio when the gate opens.",
    )
    parser.add_argument(
        "--no-echo-cancel",
        action="store_true",
        help=(
            "Disable the half-duplex echo gate that mutes the microphone "
            "while the speakers are playing. Recommended only when using "
            "headphones (no acoustic feedback path)."
        ),
    )
    parser.add_argument(
        "--prebuffer-ms",
        type=int,
        default=PLAYER_PREBUFFER_MS,
        help=(
            "Player jitter buffer size. Higher absorbs more network jitter "
            "at the cost of added latency. 0 disables warmup."
        ),
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# run / main
# ---------------------------------------------------------------------------


async def run(
    args: list[str],
    *,
    sd=None,
    client_factory=None,
    sonic_session_factory=None,
    capturer_factory=None,
    player_factory=None,
    logger: Optional[ConsoleLogger] = None,
    stderr=None,
) -> int:
    """Run the demo lifecycle.

    Returns a process exit code. Any fatal failure is reported on
    ``stderr`` and translated to one of the documented codes (2, 3, 4, 5).
    The happy path returns 0 once shutdown has completed.
    """

    stderr = stderr if stderr is not None else sys.stderr
    logger = logger or ConsoleLogger()
    sonic_session_factory = sonic_session_factory or _default_session_factory
    capturer_factory = capturer_factory or _default_capturer_factory
    player_factory = player_factory or _default_player_factory

    try:
        opts = _parse_args(args)
    except SystemExit as exc:
        # argparse prints its own --help / error message to stderr.
        return int(exc.code or 0)

    # ------------------------------------------------------------------
    # 1. Probe audio devices.
    # ------------------------------------------------------------------
    try:
        probe_audio_devices(sd=sd)
    except MissingDeviceError as exc:
        print(f"Missing {exc.kind} device", file=stderr)
        return 3

    # ------------------------------------------------------------------
    # 2. Resolve and validate region.
    # ------------------------------------------------------------------
    try:
        region = resolve_region()
    except Exception as exc:  # pragma: no cover - defensive: SDK can raise.
        print(f"Failed to resolve AWS region: {exc}", file=stderr)
        return 2

    try:
        validate_region(region)
    except UnsupportedRegionError as exc:
        print(str(exc), file=stderr)
        return 2

    # ------------------------------------------------------------------
    # 3. Assert credentials.
    # ------------------------------------------------------------------
    try:
        assert_credentials_resolvable()
    except MissingCredentialsError:
        print("AWS credentials missing or invalid", file=stderr)
        return 4

    # ------------------------------------------------------------------
    # 4. Build registry / dispatcher / session.
    # ------------------------------------------------------------------
    registry = build_default_registry()
    dispatcher = ToolDispatcher(registry, logger)
    session = sonic_session_factory(
        region,
        registry,
        logger,
        dispatcher,
        client_factory=client_factory,
    )

    # ------------------------------------------------------------------
    # 5. Open session.
    # ------------------------------------------------------------------
    try:
        await session.open()
    except BedrockOpenError as exc:
        print(
            f"Bedrock open failed ({exc.category}): {exc.underlying}",
            file=stderr,
        )
        return 5

    # ------------------------------------------------------------------
    # 6. Mark active and emit banner.
    # ------------------------------------------------------------------
    logger.mark_session_active()
    logger.banner(MODEL_ID, region)

    # ------------------------------------------------------------------
    # 7. Start capturer/player and announce listening.
    # ------------------------------------------------------------------
    if opts.no_vad:
        on_frame = session.send_audio
        gate: Optional[VADGate] = None
    else:
        gate = VADGate(
            session.send_audio,
            aggressiveness=opts.vad_aggressiveness,
            frame_ms=opts.vad_frame_ms,
            batch_frames=opts.vad_batch_frames,
            hangover_ms=opts.vad_hangover_ms,
            preroll_ms=opts.vad_preroll_ms,
        )
        on_frame = gate.on_frame

    capturer = capturer_factory(on_frame=on_frame, sd=sd)
    player = player_factory(sd=sd, prebuffer_ms=opts.prebuffer_ms)

    # Wire the half-duplex echo gate from the player into the VAD gate.
    # The gate consults ``player.is_playing()`` and drops captured
    # frames while the assistant is speaking, breaking the
    # speaker -> microphone feedback loop.
    if gate is not None and not opts.no_echo_cancel:
        is_playing = getattr(player, "is_playing", None)
        if callable(is_playing):
            gate._is_speaker_active = is_playing  # type: ignore[attr-defined]

    await capturer.start()
    await player.start()
    logger.listening()

    # ------------------------------------------------------------------
    # 8. Run the event-routing loop until the user interrupts.
    # ------------------------------------------------------------------
    async def event_loop() -> None:
        async for event in session.stream_events():
            if isinstance(event, AudioOutEvent):
                await player.enqueue(event.pcm)
            elif isinstance(event, TranscriptEvent):
                stripped = event.text.strip()
                # Nova Sonic emits ``{"interrupted":true}`` (and similar
                # JSON envelopes) as ASSISTANT text to signal barge-in
                # and other protocol metadata. Don't print them; if it
                # is a barge-in, clear the playback buffer so the
                # assistant stops talking over the user.
                if stripped.startswith("{") and stripped.endswith("}"):
                    if "interrupted" in stripped and event.role == "ASSISTANT":
                        clear = getattr(player, "clear", None)
                        if callable(clear):
                            clear()
                    continue
                if event.role == "USER":
                    logger.user(event.text)
                else:
                    logger.assistant(event.text)
            # ToolUseEvent is consumed inside SonicSession.

    stop_event = asyncio.Event()
    event_task = asyncio.create_task(event_loop())

    def _request_stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    sigint_installed = False
    try:
        loop.add_signal_handler(signal.SIGINT, _request_stop)
        sigint_installed = True
    except (NotImplementedError, RuntimeError, ValueError):
        # ``add_signal_handler`` is unsupported on Windows and outside
        # the main thread. Fall back to letting ``main()`` catch
        # ``KeyboardInterrupt`` around ``asyncio.run``.
        pass

    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, pending = await asyncio.wait(
            {event_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        # Drain any remaining task exceptions/cancellations quietly so
        # they don't surface as "task exception was never retrieved".
        for task in (event_task, stop_task):
            if task.done():
                if not task.cancelled():
                    task.exception()  # consume the exception, if any
            else:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
    except (KeyboardInterrupt, asyncio.CancelledError):
        # KeyboardInterrupt should normally be caught by the SIGINT
        # handler above and converted into a clean stop_event. This
        # branch covers the fallback path on platforms where the signal
        # handler could not be installed.
        pass
    finally:
        if sigint_installed:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 9. Shutdown within the deadline.
    # ------------------------------------------------------------------
    async def _shutdown() -> None:
        try:
            await capturer.stop()
        except Exception:
            pass
        if gate is not None:
            try:
                await gate.flush()
            except Exception:
                pass
        try:
            await player.stop()
        except Exception:
            pass
        try:
            await session.close()
        except Exception:
            pass
        logger.mark_session_closed()

    try:
        await asyncio.wait_for(_shutdown(), timeout=SHUTDOWN_DEADLINE_S)
    except asyncio.TimeoutError:
        # We still report success: the shutdown did its best within
        # the deadline and the process is exiting anyway.
        pass

    return 0


def main() -> None:
    """Entry point for ``python -m nova_sonic_demo``.

    Parses ``sys.argv[1:]`` (no flags currently supported), drives
    :func:`run`, and exits with the resulting status code. A bare
    ``KeyboardInterrupt`` that escapes the inner shutdown is mapped to
    exit code 0 because the lifecycle treats Ctrl+C as a clean exit.
    """

    args = sys.argv[1:]
    try:
        rc = asyncio.run(run(args))
    except KeyboardInterrupt:
        rc = 0
    sys.exit(rc)


__all__ = ["run", "main"]
