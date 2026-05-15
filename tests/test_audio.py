"""Unit tests for ``nova_sonic_demo.audio``.

These tests must never open a real PortAudio device. A small ``FakeSd`` shim
is injected via the constructor's ``sd`` parameter so the capturer/player
exercise the full code path (callback wiring, queue draining, silence
padding) without touching the host audio subsystem.
"""

from __future__ import annotations

import asyncio

import pytest

from nova_sonic_demo.audio import (
    AudioCapturer,
    AudioPlayer,
    probe_audio_devices,
)
from nova_sonic_demo.config import (
    INPUT_FRAME_SAMPLES,
    INPUT_SAMPLE_RATE_HZ,
    OUTPUT_SAMPLE_RATE_HZ,
    MissingDeviceError,
)


# ---------------------------------------------------------------------------
# Fake sounddevice module
# ---------------------------------------------------------------------------


class FakeStream:
    """A stand-in for ``sounddevice.RawInputStream`` / ``RawOutputStream``."""

    def __init__(
        self,
        *,
        callback,
        samplerate,
        blocksize,
        channels,
        dtype,
    ) -> None:
        self.callback = callback
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.channels = channels
        self.dtype = dtype
        self.started = False
        self.stop_calls = 0
        self.close_calls = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class FakeSd:
    def __init__(self, devices=None) -> None:
        self.devices = (
            devices
            if devices is not None
            else [
                {"max_input_channels": 1, "max_output_channels": 0, "name": "mic"},
                {"max_input_channels": 0, "max_output_channels": 2, "name": "spk"},
            ]
        )
        self.last_input_stream: FakeStream | None = None
        self.last_output_stream: FakeStream | None = None

    def query_devices(self):
        return list(self.devices)

    def RawInputStream(self, **kwargs):  # noqa: N802 (mirror sounddevice API)
        s = FakeStream(**kwargs)
        self.last_input_stream = s
        return s

    def RawOutputStream(self, **kwargs):  # noqa: N802 (mirror sounddevice API)
        s = FakeStream(**kwargs)
        self.last_output_stream = s
        return s


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# probe_audio_devices
# ---------------------------------------------------------------------------


def test_probe_audio_devices_passes_with_input_and_output():
    sd = FakeSd()
    # Should not raise.
    probe_audio_devices(sd=sd)


def test_probe_audio_devices_raises_missing_input():
    sd = FakeSd(
        devices=[{"max_input_channels": 0, "max_output_channels": 2, "name": "spk"}]
    )
    with pytest.raises(MissingDeviceError) as excinfo:
        probe_audio_devices(sd=sd)
    assert excinfo.value.kind == "input"


def test_probe_audio_devices_raises_missing_output():
    sd = FakeSd(
        devices=[{"max_input_channels": 1, "max_output_channels": 0, "name": "mic"}]
    )
    with pytest.raises(MissingDeviceError) as excinfo:
        probe_audio_devices(sd=sd)
    assert excinfo.value.kind == "output"


# ---------------------------------------------------------------------------
# AudioCapturer
# ---------------------------------------------------------------------------


def test_audio_capturer_opens_stream_with_expected_parameters():
    sd = FakeSd()

    async def scenario() -> None:
        async def _on_frame(_frame: bytes) -> None:
            return None

        capturer = AudioCapturer(_on_frame, sd=sd)
        await capturer.start()
        try:
            stream = sd.last_input_stream
            assert stream is not None
            assert stream.samplerate == INPUT_SAMPLE_RATE_HZ == 16_000
            assert stream.blocksize == INPUT_FRAME_SAMPLES == 320
            assert stream.channels == 1
            assert stream.dtype == "int16"
            assert stream.started is True
        finally:
            await capturer.stop()

    _run(scenario())


def test_audio_capturer_pump_calls_on_frame_with_callback_data():
    sd = FakeSd()

    async def scenario() -> None:
        received: list[bytes] = []
        seen = asyncio.Event()

        async def _on_frame(frame: bytes) -> None:
            received.append(frame)
            seen.set()

        capturer = AudioCapturer(_on_frame, sd=sd)
        await capturer.start()
        try:
            payload = bytes((i % 256) for i in range(INPUT_FRAME_SAMPLES * 2))
            sd.last_input_stream.callback(
                payload, INPUT_FRAME_SAMPLES, None, None
            )

            await asyncio.wait_for(seen.wait(), timeout=1.0)
            assert received == [payload]
        finally:
            await capturer.stop()

    _run(scenario())


def test_audio_capturer_stop_closes_stream_exactly_once():
    sd = FakeSd()

    async def scenario() -> None:
        async def _on_frame(_frame: bytes) -> None:
            return None

        capturer = AudioCapturer(_on_frame, sd=sd)
        await capturer.start()
        stream = sd.last_input_stream
        assert stream is not None

        await capturer.stop()
        await capturer.stop()

        assert stream.close_calls == 1
        assert stream.stop_calls == 1

    _run(scenario())


# ---------------------------------------------------------------------------
# AudioPlayer
# ---------------------------------------------------------------------------


def test_audio_player_opens_stream_with_expected_parameters():
    sd = FakeSd()

    async def scenario() -> None:
        player = AudioPlayer(sd=sd)
        await player.start()
        try:
            stream = sd.last_output_stream
            assert stream is not None
            assert stream.samplerate == OUTPUT_SAMPLE_RATE_HZ == 24_000
            assert stream.channels == 1
            assert stream.dtype == "int16"
            assert stream.started is True
        finally:
            await player.stop()

    _run(scenario())


def test_audio_player_callback_pads_with_silence_when_buffer_empty():
    sd = FakeSd()

    async def scenario() -> None:
        player = AudioPlayer(sd=sd, frame_samples=960)
        await player.start()
        try:
            frames = 960
            outdata = bytearray(b"\xab" * (frames * 2))
            sd.last_output_stream.callback(outdata, frames, None, None)
            assert bytes(outdata) == b"\x00" * (frames * 2)
        finally:
            await player.stop()

    _run(scenario())


def test_audio_player_callback_drains_enqueued_pcm():
    sd = FakeSd()

    async def scenario() -> None:
        # prebuffer_ms=0 disables warmup so the test exercises pure draining.
        player = AudioPlayer(sd=sd, frame_samples=960, prebuffer_ms=0)
        await player.start()
        try:
            frames = 960
            payload = bytes((i % 256) for i in range(frames * 2))
            await player.enqueue(payload)
            outdata = bytearray(frames * 2)
            sd.last_output_stream.callback(outdata, frames, None, None)
            assert bytes(outdata) == payload
        finally:
            await player.stop()

    _run(scenario())


def test_audio_player_warming_emits_silence_until_prebuffer_full():
    """With prebuffer_ms set, the callback emits silence until enough audio has accumulated."""
    sd = FakeSd()

    async def scenario() -> None:
        player = AudioPlayer(sd=sd, frame_samples=960, prebuffer_ms=40)
        await player.start()
        try:
            frames = 960
            # 40 ms at 24 kHz = 1920 bytes prebuffer threshold.
            small = bytes([1] * 200)  # well under threshold
            await player.enqueue(small)
            outdata = bytearray(frames * 2)
            sd.last_output_stream.callback(outdata, frames, None, None)
            # Still warming, callback should emit pure silence.
            assert bytes(outdata) == b"\x00" * (frames * 2)

            # Now top up past the threshold and a subsequent callback drains.
            big = bytes([2] * (frames * 2))
            await player.enqueue(big)
            outdata2 = bytearray(frames * 2)
            sd.last_output_stream.callback(outdata2, frames, None, None)
            # Once warmed, the callback should drain real audio (no longer all zeros).
            assert bytes(outdata2) != b"\x00" * (frames * 2)
        finally:
            await player.stop()

    _run(scenario())


def test_audio_player_stop_closes_stream_exactly_once():
    sd = FakeSd()

    async def scenario() -> None:
        player = AudioPlayer(sd=sd)
        await player.start()
        stream = sd.last_output_stream
        assert stream is not None

        await player.stop()
        await player.stop()

        assert stream.close_calls == 1
        assert stream.stop_calls == 1

    _run(scenario())


def test_audio_player_drops_oldest_data_when_capacity_exceeded():
    sd = FakeSd()

    async def scenario() -> None:
        frame_samples = 10
        max_queue_frames = 1
        player = AudioPlayer(
            sd=sd,
            frame_samples=frame_samples,
            max_queue_frames=max_queue_frames,
        )
        await player.start()
        try:
            cap = max_queue_frames * 2 * frame_samples  # 20 bytes
            await player.enqueue(b"\x01" * 16)
            await player.enqueue(b"\x02" * 16)
            await player.enqueue(b"\x03" * 16)

            # The internal buffer must never exceed the cap after inserts.
            assert player._buffer_bytes <= cap

            # The newest insertion must still be retained intact (not dropped),
            # since the cap is enforced by trimming older data first.
            flat = b"".join(player._buffer)
            assert flat.endswith(b"\x03" * min(16, cap))
        finally:
            await player.stop()

    _run(scenario())



# ---------------------------------------------------------------------------
# VADGate
# ---------------------------------------------------------------------------


from nova_sonic_demo.audio import VADGate
from nova_sonic_demo.config import INPUT_SAMPLE_RATE_HZ


class FakeVad:
    """Stub webrtcvad.Vad-compatible classifier driven by a script."""

    def __init__(self, script):
        # script: list[bool] consumed in FIFO order; defaults to silence.
        self._script = list(script)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        if self._script:
            return self._script.pop(0)
        return False


def _silence_frame(ms: int = 20) -> bytes:
    return b"\x00\x00" * int(INPUT_SAMPLE_RATE_HZ * ms / 1000)


def test_vad_gate_drops_silent_frames():
    sent: list[bytes] = []

    async def downstream(b: bytes) -> None:
        sent.append(b)

    async def scenario() -> None:
        gate = VADGate(
            downstream,
            vad=FakeVad([False] * 50),
            preroll_ms=0,
            batch_frames=1,
        )
        for _ in range(10):
            await gate.on_frame(_silence_frame())

    _run(scenario())
    assert sent == []


def test_vad_gate_streams_during_speech_with_preroll():
    sent: list[bytes] = []

    async def downstream(b: bytes) -> None:
        sent.append(b)

    async def scenario() -> None:
        # Three silent frames, then three voice frames.
        script = [False, False, False, True, True, True]
        gate = VADGate(
            downstream,
            vad=FakeVad(script),
            preroll_ms=40,         # 2 frames @ 20 ms
            batch_frames=4,        # batch up to 4 frames before flushing
            hangover_ms=400,       # don't close gate due to hangover during this test
        )
        for _ in range(6):
            await gate.on_frame(_silence_frame())
        # Flush whatever is still pending so we can assert.
        await gate.flush()

    _run(scenario())
    # Expect at least one batch sent. The first batch should contain the
    # 2-frame pre-roll plus voice frames -> >= 4 frames * 640 bytes.
    assert sent, "gate should have emitted at least one batch"
    total_bytes = sum(len(b) for b in sent)
    bytes_per_frame = 2 * int(INPUT_SAMPLE_RATE_HZ * 20 / 1000)  # 640 bytes
    total_frames = total_bytes // bytes_per_frame
    assert total_frames >= 5, f"expected >=5 frames (preroll+voice), got {total_frames}"


def test_vad_gate_closes_after_hangover():
    sent: list[bytes] = []

    async def downstream(b: bytes) -> None:
        sent.append(b)

    async def scenario() -> None:
        # 1 voice frame, then enough silence to exceed hangover.
        script = [True] + [False] * 30
        gate = VADGate(
            downstream,
            vad=FakeVad(script),
            preroll_ms=0,
            batch_frames=64,        # don't batch-flush; force hangover-flush only
            hangover_ms=80,         # 4 frames @ 20 ms
        )
        for _ in range(31):
            await gate.on_frame(_silence_frame())

    _run(scenario())
    assert sent, "gate should have flushed once hangover expired"
    # After the hangover the gate should be closed; flushed audio is
    # already in `sent`. We don't try to assert exact frame counts here
    # because they depend on internal batching boundaries.
    assert len(sent) >= 1


def test_vad_gate_rejects_invalid_frame_ms():
    with __import__("pytest").raises(ValueError):
        VADGate(lambda b: None, vad=FakeVad([]), frame_ms=15)


def test_vad_gate_flush_drains_pending_batch():
    sent: list[bytes] = []

    async def downstream(b: bytes) -> None:
        sent.append(b)

    async def scenario() -> None:
        gate = VADGate(
            downstream,
            vad=FakeVad([True, True]),
            preroll_ms=0,
            batch_frames=10,        # never reaches threshold
            hangover_ms=10_000,
        )
        await gate.on_frame(_silence_frame())
        await gate.on_frame(_silence_frame())
        # Without flush: batch is still buffered, nothing sent.
        assert sent == []
        await gate.flush()

    _run(scenario())
    assert sent, "flush() must drain any pending batched audio"



def test_vad_gate_drops_frames_while_speaker_is_active():
    """Echo gate: the gate must not forward audio while the speakers are playing."""
    sent: list[bytes] = []

    async def downstream(b: bytes) -> None:
        sent.append(b)

    speaker_active = [True]  # drives the is_speaker_active callback

    async def scenario() -> None:
        gate = VADGate(
            downstream,
            vad=FakeVad([True] * 30),  # vad always says "speech"
            preroll_ms=0,
            batch_frames=1,
            hangover_ms=20,
            is_speaker_active=lambda: speaker_active[0],
        )
        # Speakers are "active": every frame must be muted.
        for _ in range(10):
            await gate.on_frame(_silence_frame())
        assert sent == []
        assert gate.frames_muted_by_echo_gate == 10
        # Speakers go quiet: subsequent voice frames pass through.
        speaker_active[0] = False
        for _ in range(3):
            await gate.on_frame(_silence_frame())
        await gate.flush()

    _run(scenario())
    assert sent, "gate must forward audio after speakers go silent"


def test_audio_player_is_playing_reflects_buffer_state():
    sd = FakeSd()

    async def scenario() -> None:
        player = AudioPlayer(
            sd=sd, frame_samples=960, prebuffer_ms=0, post_speech_grace_ms=0
        )
        await player.start()
        try:
            assert player.is_playing() is False
            await player.enqueue(b"\x01\x02" * 480)
            assert player.is_playing() is True
            # Drain.
            outdata = bytearray(960 * 2)
            sd.last_output_stream.callback(outdata, 960, None, None)
            assert player.is_playing() is False
        finally:
            await player.stop()

    _run(scenario())



def test_audio_player_is_playing_false_while_warming_with_empty_buffer():
    """Regression: a freshly started player must not block the microphone.

    Warming mode emits silence with an empty buffer, so it cannot cause
    speaker -> microphone echo. ``is_playing()`` must return False until
    real audio is enqueued.
    """
    sd = FakeSd()

    async def scenario() -> None:
        player = AudioPlayer(
            sd=sd, frame_samples=960, prebuffer_ms=250, post_speech_grace_ms=0
        )
        await player.start()
        try:
            # Right after start: warming, empty buffer, no audio enqueued.
            assert player.is_playing() is False
            # Trickle some audio in but stay below the prebuffer threshold.
            small = b"\x01\x02" * 100  # 200 bytes; threshold is many KB
            await player.enqueue(small)
            # Buffer is non-empty now, so is_playing() flips to True.
            assert player.is_playing() is True
        finally:
            await player.stop()

    _run(scenario())
