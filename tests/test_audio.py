"""PCM handling tests.

The firmware's own audio logic (VAD, pre-roll, mouth envelope, buffer thresholds) is
tested in C++ at tests/firmware/. This file covers the Python side: utterance assembly,
format conversion, and the chunk boundaries the wire protocol depends on.
"""

from __future__ import annotations

import pytest

from backend.audio import (
    chunk_pcm,
    pcm_duration_ms,
    pcm_to_wav,
    rms,
    silence,
    tone,
    wav_to_pcm,
)
from backend.protocol import AUDIO_SAMPLE_RATE, MAX_UTTERANCE_BYTES


def test_duration_matches_the_sample_count() -> None:
    assert pcm_duration_ms(tone(1000)) == 1000
    assert pcm_duration_ms(silence(250)) == 250
    assert pcm_duration_ms(b"") == 0


def test_wav_round_trip_is_lossless() -> None:
    original = tone(300, frequency_hz=440.0)
    recovered, rate = wav_to_pcm(pcm_to_wav(original))
    assert recovered == original
    assert rate == AUDIO_SAMPLE_RATE


def test_stereo_wav_is_mixed_down_to_mono() -> None:
    """Some TTS providers return stereo. The robot has one speaker."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(AUDIO_SAMPLE_RATE)
        handle.writeframes(b"\x00\x10\x00\x10" * 100)
    pcm, rate = wav_to_pcm(buffer.getvalue())
    assert len(pcm) == 200  # 100 frames, one channel, 2 bytes each
    assert rate == AUDIO_SAMPLE_RATE


def test_unsupported_bit_depth_is_refused_not_silently_converted() -> None:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(1)  # 8-bit
        handle.setframerate(AUDIO_SAMPLE_RATE)
        handle.writeframes(b"\x80" * 100)
    with pytest.raises(ValueError, match="16-bit"):
        wav_to_pcm(buffer.getvalue())


def test_rms_of_known_signals() -> None:
    assert rms(silence(100)) == 0.0
    # A sine at amplitude a has RMS a/sqrt(2).
    measured = rms(tone(100, amplitude=0.5))
    assert 0.34 < measured < 0.36
    assert rms(b"") == 0.0


@pytest.mark.parametrize("chunk_bytes", [1, 2, 3, 511, 1024, 2048, 100_000])
def test_chunks_never_split_a_sample(chunk_bytes: int) -> None:
    """Half a sample on the wire is an audible click."""
    pcm = tone(500)
    chunks = chunk_pcm(pcm, chunk_bytes)
    assert all(len(chunk) % 2 == 0 for chunk in chunks)
    assert b"".join(chunks) == pcm


def test_chunking_preserves_order_and_content() -> None:
    pcm = tone(1000, frequency_hz=300.0)
    assert b"".join(chunk_pcm(pcm, 2048)) == pcm


def test_chunking_rejects_a_nonsense_size() -> None:
    with pytest.raises(ValueError):
        chunk_pcm(tone(100), 0)


def test_empty_audio_produces_no_chunks() -> None:
    assert chunk_pcm(b"", 2048) == []


def test_utterance_cap_covers_a_realistic_sentence() -> None:
    """The cap must be generous enough for real speech and tight enough to bound memory."""
    seconds = MAX_UTTERANCE_BYTES / (AUDIO_SAMPLE_RATE * 2)
    assert seconds >= 15, "too short for a normal spoken sentence"
    assert MAX_UTTERANCE_BYTES <= 2 * 1024 * 1024, "one utterance should not cost megabytes"


def test_wire_chunk_size_is_a_whole_number_of_samples() -> None:
    """2048 bytes is exactly 1024 samples, or 64 ms — a clean cadence at 16 kHz."""
    from backend.protocol import MAX_BINARY_FRAME_BYTES

    assert MAX_BINARY_FRAME_BYTES % 2 == 0
    assert pcm_duration_ms(b"\x00" * 2048) == 64
