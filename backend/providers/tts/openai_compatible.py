"""TTS against any OpenAI-compatible `/audio/speech` endpoint.

Requests WAV and decodes it to the raw PCM the wire protocol carries. Asking for WAV
rather than MP3 is deliberate: decoding MP3 would drag in a native codec dependency for
no benefit, since the robot can only play PCM anyway.

> **NOT LIVE-API VERIFIED.** Unit tested against a mocked transport only.
"""

from __future__ import annotations

from typing import AsyncIterator

from backend.audio import chunk_pcm, wav_to_pcm
from backend.errors import ErrorCategory, ProviderError
from backend.providers.base import AudioChunk
from backend.providers.http import describe_http_error, get_client

_CHUNK_BYTES = 2_048


class OpenAICompatibleTTS:
    """POST text, receive WAV, yield PCM chunks.

    Non-streaming today: the whole file arrives before the first chunk is yielded. The
    async-iterator signature means a streaming implementation can replace this class
    without any caller noticing.
    """

    name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key: str, model: str, default_voice: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._default_voice = default_voice

    async def synthesize(
        self,
        text: str,
        *,
        sample_rate: int,
        voice: str | None = None,
        speed: float = 1.0,
        language: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        body = {
            "model": self._model,
            "input": text,
            "voice": voice or self._default_voice,
            "response_format": "wav",
            "speed": speed,
        }
        try:
            response = await get_client().post(
                f"{self._base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            audio = response.content
        except Exception as exc:
            raise ProviderError(
                f"TTS request failed: {describe_http_error(exc)}",
                category=ErrorCategory.TTS_FAILED,
            ) from exc

        try:
            pcm, actual_rate = wav_to_pcm(audio)
        except Exception as exc:
            raise ProviderError(
                f"TTS returned audio we cannot decode: {exc}",
                category=ErrorCategory.TTS_FAILED,
            ) from exc

        # The robot's I2S clock is fixed at the negotiated rate. Rather than resample
        # (and ship a DSP dependency for it), we report the mismatch and let the
        # operator configure a provider voice at the right rate.
        if actual_rate != sample_rate:
            raise ProviderError(
                f"TTS returned {actual_rate} Hz audio but the robot expects {sample_rate} Hz; "
                "configure a provider/voice that returns the expected rate",
                category=ErrorCategory.TTS_FAILED,
            )

        for piece in chunk_pcm(pcm, _CHUNK_BYTES):
            yield AudioChunk(pcm=piece, sample_rate=sample_rate)
