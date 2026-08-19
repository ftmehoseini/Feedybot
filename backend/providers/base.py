"""Provider interfaces.

Three `Protocol` classes, structurally typed, no base class to inherit. Business logic
depends on these and nothing else; every vendor detail lives behind one of them.

The interfaces are shaped for *today's* complete-response pipeline, but each one is
designed so a streaming variant can be added as an extra method without changing a
single existing caller. That is the whole reason `synthesize` returns chunks rather than
one `bytes` blob — the pipeline already forwards audio incrementally, so a streaming TTS
adapter drops in without touching `pipeline.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class Transcript:
    """The result of speech recognition."""

    text: str
    #: BCP-47-ish short code ("fa", "en") when the provider reports one. `None` means
    #: the provider did not tell us — the caller must not invent a value.
    language: str | None = None
    #: Provider-reported confidence in [0, 1] when available. Advisory only.
    confidence: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True)
class LLMResult:
    """The raw text a model produced, before it becomes an `AgentReply`.

    Deliberately untyped beyond `text`: parsing model conventions is
    `agent_reply.parse_agent_output`'s job, not a provider's. A provider that tried to
    parse emotion markers would have to be updated every time the prompt changed.
    """

    text: str
    model: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioChunk:
    """One piece of PCM, ready to put on the wire.

    Always 16-bit signed little-endian mono at `sample_rate`. Providers are responsible
    for decoding whatever they natively return into this format, so that nothing
    downstream — including the firmware — ever has to know what a container format is.
    """

    pcm: bytes
    sample_rate: int


@runtime_checkable
class STTProvider(Protocol):
    """Speech to text."""

    name: str

    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        language_hint: Sequence[str] | None = None,
    ) -> Transcript:
        """Transcribe 16-bit mono PCM.

        Args:
            pcm: raw little-endian 16-bit mono samples.
            sample_rate: samples per second.
            language_hint: languages the active role expects, best-first. A hint, not a
                constraint — the provider may return something else, and the caller must
                cope.

        Raises:
            ProviderError: on any failure the caller should treat as "STT did not work".
        """
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Text generation."""

    name: str

    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_output_tokens: int = 300,
    ) -> LLMResult:
        """Generate a reply from an OpenAI-style message list.

        The message list is the lingua franca here: `[{"role": ..., "content": ...}]`.
        Adapters for APIs with a different shape (Gemini's `contents`/`parts`) translate
        internally.

        Raises:
            ProviderError: on any failure the caller should treat as "the model did not
                answer".
        """
        ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text to speech."""

    name: str

    def synthesize(
        self,
        text: str,
        *,
        sample_rate: int,
        voice: str | None = None,
        speed: float = 1.0,
        language: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Synthesise `text`, yielding PCM chunks in playback order.

        Returning an async iterator rather than one blob is the seam for streaming TTS:
        a non-streaming adapter yields exactly one chunk, a streaming one yields many,
        and the pipeline cannot tell the difference.

        Raises:
            ProviderError: on any failure the caller should treat as "no audio".
        """
        ...
