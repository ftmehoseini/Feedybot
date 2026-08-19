"""PCM utilities shared by providers, the pipeline and the tests.

Everything here operates on the one format the whole system speaks: 16-bit signed
little-endian mono PCM. Format conversion happens at the edges — inside a provider
adapter — and never in the middle of the pipeline.
"""

from __future__ import annotations

import io
import math
import struct
import wave

from backend.protocol import (
    AUDIO_BITS_PER_SAMPLE,
    AUDIO_BYTES_PER_SAMPLE,
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
)


def pcm_duration_ms(pcm: bytes, sample_rate: int = AUDIO_SAMPLE_RATE) -> int:
    """Duration of a 16-bit mono PCM buffer, in milliseconds."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    samples = len(pcm) // AUDIO_BYTES_PER_SAMPLE
    return int(samples * 1000 / sample_rate)


def pcm_to_wav(pcm: bytes, sample_rate: int = AUDIO_SAMPLE_RATE) -> bytes:
    """Wrap raw PCM in a WAV container.

    Needed because most STT HTTP APIs want a file with a header, while the wire protocol
    to the robot deliberately carries headerless PCM (a 44-byte header per utterance is
    pure overhead on an MCU, and the format is fixed anyway).
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(AUDIO_CHANNELS)
        handle.setsampwidth(AUDIO_BYTES_PER_SAMPLE)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    """Extract mono 16-bit PCM and its sample rate from a WAV file.

    Raises:
        ValueError: if the file is not 16-bit PCM. Rather than silently resampling or
            requantising, we refuse: a provider returning an unexpected format is a
            configuration problem the operator should see, not something to paper over.
    """
    with wave.open(io.BytesIO(data), "rb") as handle:
        if handle.getsampwidth() != AUDIO_BYTES_PER_SAMPLE:
            raise ValueError(
                f"expected {AUDIO_BITS_PER_SAMPLE}-bit audio, got "
                f"{handle.getsampwidth() * 8}-bit"
            )
        frames = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
        if handle.getnchannels() == 2:
            frames = _stereo_to_mono(frames)
        elif handle.getnchannels() != 1:
            raise ValueError(f"expected mono or stereo audio, got {handle.getnchannels()} channels")
    return frames, rate


def _stereo_to_mono(pcm: bytes) -> bytes:
    """Average interleaved stereo down to mono."""
    count = len(pcm) // (AUDIO_BYTES_PER_SAMPLE * 2)
    samples = struct.unpack(f"<{count * 2}h", pcm[: count * 4])
    mono = bytearray()
    for index in range(count):
        mixed = (samples[index * 2] + samples[index * 2 + 1]) // 2
        mono += struct.pack("<h", mixed)
    return bytes(mono)


def rms(pcm: bytes) -> float:
    """Root-mean-square amplitude of a PCM buffer, normalised to [0, 1].

    Used by the fake providers and the tests. The *robot* computes its own RMS on-device
    for mouth animation — the backend never tells the face how wide to open, because
    only the device knows what actually came out of the speaker.
    """
    count = len(pcm) // AUDIO_BYTES_PER_SAMPLE
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", pcm[: count * AUDIO_BYTES_PER_SAMPLE])
    total = sum(float(sample) * float(sample) for sample in samples)
    return math.sqrt(total / count) / 32768.0


def chunk_pcm(pcm: bytes, chunk_bytes: int) -> list[bytes]:
    """Split PCM into wire-sized chunks, never splitting a sample across two chunks."""
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    # Round down to a whole number of samples: half a sample on the wire is a click.
    aligned = max(AUDIO_BYTES_PER_SAMPLE, chunk_bytes - (chunk_bytes % AUDIO_BYTES_PER_SAMPLE))
    return [pcm[offset : offset + aligned] for offset in range(0, len(pcm), aligned)]


def silence(duration_ms: int, sample_rate: int = AUDIO_SAMPLE_RATE) -> bytes:
    """A buffer of digital silence. Used for padding and by the fake TTS provider."""
    return b"\x00\x00" * int(sample_rate * duration_ms / 1000)


def tone(
    duration_ms: int,
    frequency_hz: float = 220.0,
    amplitude: float = 0.25,
    sample_rate: int = AUDIO_SAMPLE_RATE,
) -> bytes:
    """A sine tone. The fake TTS provider's "voice", and a test fixture with known RMS."""
    count = int(sample_rate * duration_ms / 1000)
    peak = int(max(0.0, min(1.0, amplitude)) * 32767)
    return b"".join(
        struct.pack("<h", int(peak * math.sin(2 * math.pi * frequency_hz * n / sample_rate)))
        for n in range(count)
    )
