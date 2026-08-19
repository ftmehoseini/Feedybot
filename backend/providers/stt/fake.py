"""In-process STT for tests, the simulator, and running the backend with no API keys.

This is not a stub that returns a constant. It is scriptable, so a test can drive an
entire conversation deterministically, and it detects Persian by script so the language
plumbing is exercised for real rather than mocked away.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Sequence

from backend.audio import rms
from backend.errors import ErrorCategory, ProviderError
from backend.providers.base import Transcript

#: Persian/Arabic script range. Crude, and deliberately so: this is a test double, and
#: the real providers report language themselves.
_PERSIAN_RANGE = range(0x0600, 0x0700)


def looks_persian(text: str) -> bool:
    """True when the text is predominantly Persian/Arabic script."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    persian = sum(1 for c in letters if ord(c) in _PERSIAN_RANGE)
    return persian * 2 > len(letters)


class FakeSTTProvider:
    """Returns queued transcripts in order, then falls back to a default.

    Args:
        script: transcripts to return, one per call, in order.
        default: returned once the script is exhausted.
        fail_after: raise `ProviderError` starting from this call index (0-based). Used
            to test failure handling without patching internals.
        delay_s: artificial latency, for exercising timeout paths.
    """

    name = "fake"

    def __init__(
        self,
        script: Sequence[str] | None = None,
        *,
        default: str = "Hello there.",
        fail_after: int | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self._script: deque[str] = deque(script or [])
        self._default = default
        self._fail_after = fail_after
        self._delay_s = delay_s
        self.calls = 0
        #: RMS of the last buffer received, so tests can assert real audio arrived.
        self.last_rms = 0.0

    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        language_hint: Sequence[str] | None = None,
    ) -> Transcript:
        index = self.calls
        self.calls += 1
        self.last_rms = rms(pcm)
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._fail_after is not None and index >= self._fail_after:
            raise ProviderError("fake STT failure", category=ErrorCategory.STT_FAILED)
        text = self._script.popleft() if self._script else self._default
        return Transcript(
            text=text,
            language="fa" if looks_persian(text) else "en",
            confidence=0.99,
            metadata={"provider": self.name, "input_bytes": len(pcm)},
        )
