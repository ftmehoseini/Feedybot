"""In-process TTS for tests, the simulator, and key-less local runs.

Produces a real PCM buffer — a quiet tone whose length scales with the text — so that
playback timing, chunking and the amplitude-driven mouth can all be exercised without a
speech API. It yields several chunks rather than one, which keeps the streaming code
path honest even though the "synthesis" is instantaneous.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from backend.audio import chunk_pcm, tone
from backend.errors import ErrorCategory, ProviderError
from backend.providers.base import AudioChunk

#: Roughly conversational pace. Only used to give the fake a plausible duration.
_MS_PER_CHARACTER = 55
_MIN_MS = 300
_MAX_MS = 8_000
_CHUNK_BYTES = 2_048


class FakeTTSProvider:
    """Synthesises a tone whose duration tracks the text length.

    Args:
        fail: raise `ProviderError` on every call.
        delay_s: artificial latency before the first chunk, for timeout tests.
        chunk_delay_s: artificial latency between chunks, for cancellation tests.
        frequency_hz: pitch of the generated tone.
    """

    name = "fake"

    def __init__(
        self,
        *,
        fail: bool = False,
        delay_s: float = 0.0,
        chunk_delay_s: float = 0.0,
        frequency_hz: float = 220.0,
    ) -> None:
        self._fail = fail
        self._delay_s = delay_s
        self._chunk_delay_s = chunk_delay_s
        self._frequency_hz = frequency_hz
        self.calls = 0
        self.last_text = ""

    async def synthesize(
        self,
        text: str,
        *,
        sample_rate: int,
        voice: str | None = None,
        speed: float = 1.0,
        language: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls += 1
        self.last_text = text
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._fail:
            raise ProviderError("fake TTS failure", category=ErrorCategory.TTS_FAILED)

        duration_ms = max(_MIN_MS, min(_MAX_MS, int(len(text) * _MS_PER_CHARACTER / max(speed, 0.1))))
        pcm = tone(duration_ms, frequency_hz=self._frequency_hz, sample_rate=sample_rate)
        for piece in chunk_pcm(pcm, _CHUNK_BYTES):
            # Yield to the event loop between chunks, as a real streaming provider
            # awaiting a socket would. Without this the "stream" completes in one
            # uninterruptible step and cancellation could never land mid-reply.
            await asyncio.sleep(self._chunk_delay_s)
            yield AudioChunk(pcm=piece, sample_rate=sample_rate)
