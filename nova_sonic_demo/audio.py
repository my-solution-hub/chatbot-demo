"""Audio_Capturer and Audio_Player implementations (task 9).

Both classes wrap ``sounddevice`` raw streams so the rest of the demo can
push and pull bytes without worrying about the PortAudio threading model:

* :class:`AudioCapturer` opens a 16 kHz / 16-bit / mono ``RawInputStream``.
  The PortAudio callback runs on a background thread and is bridged onto
  the asyncio loop via ``loop.call_soon_threadsafe``. A pump task drains
  a bounded :class:`asyncio.Queue` and forwards each frame to the user
  supplied ``on_frame`` coroutine.
* :class:`AudioPlayer` opens a 24 kHz / 16-bit / mono ``RawOutputStream``.
  The PortAudio callback drains a thread-safe byte buffer that is fed by
  :meth:`AudioPlayer.enqueue`. When the buffer is empty the callback writes
  silence so the device never underruns.

The ``sounddevice`` module is imported lazily so the rest of the package
(and the unit tests) can run without PortAudio installed. Tests inject a
fake ``sd`` module via the constructor parameter.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Awaitable, Callable, List, Optional

from .config import (
    INPUT_FRAME_SAMPLES,
    INPUT_SAMPLE_RATE_HZ,
    OUTPUT_SAMPLE_RATE_HZ,
    PLAYER_PREBUFFER_MS,
    VAD_AGGRESSIVENESS,
    VAD_BATCH_FRAMES,
    VAD_FRAME_MS,
    VAD_HANGOVER_MS,
    VAD_PREROLL_MS,
    MissingDeviceError,
)


# Number of bytes per 16-bit signed PCM sample.
_BYTES_PER_SAMPLE = 2


def _import_sounddevice():  # pragma: no cover - thin SDK adapter
    """Import ``sounddevice`` lazily so the package stays import-light."""
    import sounddevice

    return sounddevice


def _max_channels(device: object, key: str) -> int:
    """Return ``device[key]`` whether ``device`` is a dict or attribute bag."""
    if isinstance(device, dict):
        return int(device.get(key, 0) or 0)
    return int(getattr(device, key, 0) or 0)


def probe_audio_devices(sd=None) -> None:
    """Raise :class:`MissingDeviceError` if input or output is unavailable.

    The check examines ``sd.query_devices()`` and considers any device with
    ``max_input_channels > 0`` to be a valid input and any device with
    ``max_output_channels > 0`` to be a valid output. ``sd`` may be injected
    for testing; when omitted the real ``sounddevice`` module is imported.
    """
    sd = sd or _import_sounddevice()
    try:
        devices = sd.query_devices()
    except Exception as exc:  # pragma: no cover - depends on host audio
        raise MissingDeviceError("input") from exc

    has_input = any(_max_channels(d, "max_input_channels") > 0 for d in devices)
    has_output = any(
        _max_channels(d, "max_output_channels") > 0 for d in devices
    )

    if not has_input:
        raise MissingDeviceError("input")
    if not has_output:
        raise MissingDeviceError("output")


class AudioCapturer:
    """Capture 16 kHz / 16-bit / mono PCM and forward frames to ``on_frame``.

    The PortAudio callback runs on a background thread. Each callback
    schedules ``_enqueue_frame`` on the asyncio loop using
    ``loop.call_soon_threadsafe``. The async pump task awaits the queue and
    calls ``on_frame(frame)``; exceptions raised by ``on_frame`` are swallowed
    so a misbehaving downstream consumer cannot crash the demo.
    """

    def __init__(
        self,
        on_frame: Callable[[bytes], Awaitable[None]],
        *,
        sd=None,
        max_queue_frames: int = 50,
    ) -> None:
        self._on_frame = on_frame
        self._sd = sd or _import_sounddevice()
        self._queue: asyncio.Queue = asyncio.Queue(max_queue_frames)
        self._stream = None
        self._pump_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._started = False
        self._stopped = False

    async def start(self) -> None:
        """Open the input stream and start the pump task."""
        if self._started:
            return
        self._loop = asyncio.get_running_loop()

        def _callback(indata, frames, time_info, status):
            # ``indata`` is buffer-like (bytes/bytearray/CFFI buffer) carrying
            # 16-bit signed PCM, since dtype='int16' is requested below.
            try:
                data = bytes(indata)
            except Exception:
                return
            loop = self._loop
            if loop is None or loop.is_closed():
                return
            loop.call_soon_threadsafe(self._enqueue_frame, data)

        self._stream = self._sd.RawInputStream(
            samplerate=INPUT_SAMPLE_RATE_HZ,
            blocksize=INPUT_FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            callback=_callback,
        )
        self._stream.start()
        self._pump_task = asyncio.create_task(self._pump())
        self._started = True

    def _enqueue_frame(self, frame: bytes) -> None:
        """Push ``frame`` onto the queue, dropping the oldest on overflow."""
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Drop the oldest frame to keep latency bounded.
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(frame)
            except asyncio.QueueFull:  # pragma: no cover - racy fallback
                pass

    async def _pump(self) -> None:
        while True:
            frame = await self._queue.get()
            if frame is None:
                return
            try:
                await self._on_frame(frame)
            except Exception:
                # Per design.md, the pump must not crash the demo.
                continue

    async def stop(self) -> None:
        """Stop the stream and the pump. Idempotent."""
        if self._stopped:
            return
        self._stopped = True
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        # Sentinel to wake the pump so it can exit cleanly.
        await self._queue.put(None)
        if self._pump_task is not None:
            try:
                await self._pump_task
            except asyncio.CancelledError:  # pragma: no cover
                pass


class AudioPlayer:
    """Play 24 kHz / 16-bit / mono PCM with underrun-safe silence padding.

    Bytes enqueued via :meth:`enqueue` are buffered in a thread-safe list of
    ``bytes`` chunks. The PortAudio callback (running on a background
    thread) drains exactly the number of bytes requested per call and pads
    with zeros if the buffer cannot satisfy the request, so the device never
    underruns audibly.

    A jitter buffer (``prebuffer_ms``) hides cross-region network jitter:
    after start (or after an underrun) the player stays in a "warming"
    state, emitting silence, until that many milliseconds of audio have
    accumulated. Once warm, playback proceeds normally; an underrun
    flips the state back to warming so the next round can absorb a fresh
    burst of jitter without stuttering.
    """

    def __init__(
        self,
        *,
        sd=None,
        max_queue_frames: int = 200,
        frame_samples: int = 960,  # 40 ms at 24 kHz
        prebuffer_ms: int = PLAYER_PREBUFFER_MS,
        post_speech_grace_ms: int = 300,
    ) -> None:
        self._sd = sd or _import_sounddevice()
        self._frame_samples = frame_samples
        self._max_queue_frames = max_queue_frames
        self._prebuffer_bytes = max(
            0,
            int(OUTPUT_SAMPLE_RATE_HZ * prebuffer_ms / 1000) * _BYTES_PER_SAMPLE,
        )
        self._post_speech_grace_s = max(0.0, post_speech_grace_ms / 1000.0)
        self._lock = threading.Lock()
        self._buffer: List[bytes] = []
        self._buffer_bytes = 0
        self._warming = self._prebuffer_bytes > 0
        # Wall-clock time at which the buffer most recently held audio.
        # Used by ``is_playing()`` to enforce a short post-speech grace
        # window during which the microphone is treated as still hearing
        # the assistant.
        self._last_audio_seen_t = 0.0
        self._stream = None
        self._started = False
        self._stopped = False

    async def start(self) -> None:
        """Open the output stream."""
        if self._started:
            return

        def _callback(outdata, frames, time_info, status):
            needed = frames * _BYTES_PER_SAMPLE
            with self._lock:
                # While warming, emit silence until the prebuffer is full.
                if self._warming:
                    if self._buffer_bytes >= self._prebuffer_bytes:
                        self._warming = False
                    else:
                        outdata[:] = b"\x00" * needed
                        return

                out = b""
                while self._buffer and len(out) < needed:
                    chunk = self._buffer[0]
                    take = min(needed - len(out), len(chunk))
                    out += chunk[:take]
                    if take == len(chunk):
                        self._buffer.pop(0)
                    else:
                        self._buffer[0] = chunk[take:]
                    self._buffer_bytes -= take

                # On underrun, pad with silence and re-enter warming so the
                # next batch of audio can absorb fresh network jitter.
                if len(out) < needed:
                    out = out + b"\x00" * (needed - len(out))
                    if self._prebuffer_bytes > 0:
                        self._warming = True
            outdata[:] = out

        self._stream = self._sd.RawOutputStream(
            samplerate=OUTPUT_SAMPLE_RATE_HZ,
            blocksize=self._frame_samples,
            channels=1,
            dtype="int16",
            callback=_callback,
        )
        self._stream.start()
        self._started = True

    async def enqueue(self, pcm: bytes) -> None:
        """Append ``pcm`` to the playback buffer, dropping oldest on overflow."""
        if not pcm:
            return
        cap = self._max_queue_frames * _BYTES_PER_SAMPLE * self._frame_samples
        with self._lock:
            if self._buffer_bytes + len(pcm) > cap:
                drop = self._buffer_bytes + len(pcm) - cap
                while drop > 0 and self._buffer:
                    chunk = self._buffer[0]
                    if len(chunk) <= drop:
                        self._buffer.pop(0)
                        self._buffer_bytes -= len(chunk)
                        drop -= len(chunk)
                    else:
                        self._buffer[0] = chunk[drop:]
                        self._buffer_bytes -= drop
                        drop = 0
            self._buffer.append(pcm)
            self._buffer_bytes += len(pcm)
            self._last_audio_seen_t = time.monotonic()

    def is_playing(self) -> bool:
        """Return True while audio is actively being played (or just was).

        Used by the capture-side echo gate to suppress the microphone
        while the speakers are producing assistant audio. The grace
        window keeps the gate closed briefly after the buffer drains so
        the room reverb tail isn't mistaken for the user starting to
        talk.

        ``_warming`` is intentionally NOT consulted here: a freshly
        started player sits in warming mode emitting silence with an
        empty buffer, and during that period the speakers cannot cause
        echo. Treating warming as "playing" would mute the microphone
        from the moment the demo started, before the assistant has even
        spoken.
        """

        with self._lock:
            if self._buffer_bytes > 0:
                return True
            return (
                time.monotonic() - self._last_audio_seen_t
                < self._post_speech_grace_s
            )

    async def stop(self) -> None:
        """Stop and close the underlying stream. Idempotent."""
        if self._stopped:
            return
        self._stopped = True
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def clear(self) -> None:
        """Drop any buffered audio immediately.

        Used to honor barge-in: when Nova Sonic notifies us that the
        user has interrupted, we stop playback of the in-flight
        assistant utterance so the conversation can move on without
        the model talking over the user.
        """
        with self._lock:
            self._buffer.clear()
            self._buffer_bytes = 0
            if self._prebuffer_bytes > 0:
                self._warming = True



# ---------------------------------------------------------------------------
# Voice activity gate
# ---------------------------------------------------------------------------


def _frame_bytes_for_ms(ms: int) -> int:
    """Number of int16 PCM bytes for ``ms`` of input audio."""
    return int(INPUT_SAMPLE_RATE_HZ * ms / 1000) * _BYTES_PER_SAMPLE


class VADGate:
    """Drop-in shim between :class:`AudioCapturer` and :py:meth:`SonicSession.send_audio`.

    When VAD detects silence, audio frames are buffered as a small
    pre-roll and not sent. Once a voice frame is seen, the pre-roll plus
    subsequent frames are batched (default 80 ms) into a single
    ``audioInput`` event. After ``hangover_ms`` of continuous silence the
    gate closes and the buffer is dropped, so cross-region bandwidth is
    only spent while someone is actually talking.

    Why batch: each ``audioInput`` event carries an ~100-byte JSON
    envelope plus HTTP/2 frame overhead. Sending one 80 ms chunk per
    HTTP/2 message instead of four 20 ms ones cuts that fixed cost by 4x
    without measurably increasing end-to-end latency.

    The gate accepts frames of any size, splits them into VAD-sized
    chunks (10/20/30 ms — webrtcvad does not accept anything else), and
    flushes batches at the configured boundary. ``flush()`` drains any
    pending audio, e.g. on shutdown.
    """

    def __init__(
        self,
        downstream: Callable[[bytes], "Awaitable[None]"],
        *,
        vad=None,                                  # injectable for tests
        aggressiveness: int = VAD_AGGRESSIVENESS,
        frame_ms: int = VAD_FRAME_MS,
        batch_frames: int = VAD_BATCH_FRAMES,
        hangover_ms: int = VAD_HANGOVER_MS,
        preroll_ms: int = VAD_PREROLL_MS,
        sample_rate_hz: int = INPUT_SAMPLE_RATE_HZ,
        is_speaker_active: Optional[Callable[[], bool]] = None,
    ) -> None:
        if frame_ms not in (10, 20, 30):
            raise ValueError("frame_ms must be 10, 20, or 30 (webrtcvad limit)")
        self._downstream = downstream
        self._frame_ms = frame_ms
        self._frame_bytes = _frame_bytes_for_ms(frame_ms)
        self._batch_frames = max(1, batch_frames)
        self._hangover_frames = max(1, hangover_ms // frame_ms)
        self._preroll_frames = max(0, preroll_ms // frame_ms)
        self._sample_rate_hz = sample_rate_hz
        # Half-duplex echo gate: when the speakers are playing the
        # assistant's voice we drop captured frames entirely so the
        # microphone cannot feed the assistant's own audio back to
        # Bedrock as if it were the user talking.
        self._is_speaker_active = is_speaker_active

        if vad is None:
            try:
                import webrtcvad as _wv
            except ImportError as exc:
                raise RuntimeError(
                    "webrtcvad is required for VADGate; "
                    "pass vad= to inject a stub or install webrtcvad"
                ) from exc
            vad = _wv.Vad(aggressiveness)
        self._vad = vad

        # Splitter: accumulate raw bytes and emit VAD-sized frames.
        self._split_buf = bytearray()
        # Rolling pre-roll buffer of recent silent frames.
        self._preroll: List[bytes] = []
        # Active batch (only filled while gate is open).
        self._batch: List[bytes] = []
        self._silent_run = 0
        self._gate_open = False
        # Counters for tests / observability.
        self.frames_seen = 0
        self.frames_sent = 0
        self.batches_sent = 0
        self.openings = 0
        self.frames_muted_by_echo_gate = 0

    @property
    def gate_open(self) -> bool:
        return self._gate_open

    async def on_frame(self, pcm: bytes) -> None:
        """Feed one chunk of audio (any size) into the gate."""
        if not pcm:
            return
        self._split_buf.extend(pcm)
        while len(self._split_buf) >= self._frame_bytes:
            frame = bytes(self._split_buf[: self._frame_bytes])
            del self._split_buf[: self._frame_bytes]
            await self._on_vad_frame(frame)

    async def flush(self) -> None:
        """Emit any pending batched audio. Safe to call repeatedly."""
        await self._emit_batch()
        self._batch.clear()
        self._preroll.clear()
        self._gate_open = False
        self._silent_run = 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _on_vad_frame(self, frame: bytes) -> None:
        self.frames_seen += 1

        # Half-duplex echo suppression: while the speakers are active
        # (or just finished playing), discard captured frames so the
        # microphone cannot feed the assistant's own audio back to
        # Bedrock. This is a coarse alternative to true AEC; users on
        # headphones can disable it via --no-echo-cancel.
        if self._is_speaker_active is not None and self._is_speaker_active():
            self.frames_muted_by_echo_gate += 1
            # Treat as silence: drain hangover, drop pre-roll.
            if self._gate_open:
                self._silent_run += 1
                if self._silent_run >= self._hangover_frames:
                    await self._emit_batch()
                    self._gate_open = False
                    self._silent_run = 0
            else:
                self._preroll.clear()
            return

        is_speech = self._vad.is_speech(frame, self._sample_rate_hz)

        if is_speech:
            if not self._gate_open:
                self._gate_open = True
                self.openings += 1
                # Drain pre-roll into the batch so the model hears the
                # first ~200 ms that arrived just before the gate fired.
                self._batch.extend(self._preroll)
                self._preroll.clear()
            self._silent_run = 0
            self._batch.append(frame)
        else:
            if self._gate_open:
                # Keep streaming during the hangover so we don't clip
                # natural pauses at the end of a sentence.
                self._batch.append(frame)
                self._silent_run += 1
                if self._silent_run >= self._hangover_frames:
                    await self._emit_batch()
                    self._gate_open = False
                    self._silent_run = 0
            else:
                # Maintain a short pre-roll so we don't lose the first
                # phoneme when the user starts speaking.
                if self._preroll_frames > 0:
                    self._preroll.append(frame)
                    if len(self._preroll) > self._preroll_frames:
                        self._preroll.pop(0)

        if len(self._batch) >= self._batch_frames:
            await self._emit_batch()

    async def _emit_batch(self) -> None:
        if not self._batch:
            return
        chunk = b"".join(self._batch)
        self._batch.clear()
        self.frames_sent += len(chunk) // self._frame_bytes
        self.batches_sent += 1
        await self._downstream(chunk)
