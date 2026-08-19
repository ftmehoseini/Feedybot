"""STT against any OpenAI-compatible `/audio/transcriptions` endpoint.

Verified against the documented request/response shape of the OpenAI audio
transcription API. Works unmodified with services that reimplement it (Groq, local
`whisper.cpp` servers, LM Studio) by pointing `STT_BASE_URL` elsewhere.

> **NOT LIVE-API VERIFIED.** No request has been made against a real endpoint from this
> environment; egress to provider hosts is not available here. The adapter is unit
> tested against a mocked transport only.
"""

from __future__ import annotations

from typing import Sequence

import httpx

from backend.audio import pcm_to_wav
from backend.errors import ErrorCategory, ProviderError
from backend.providers.base import Transcript
from backend.providers.http import describe_http_error, get_client


class OpenAICompatibleSTT:
    """Multipart upload of a WAV file to `{base_url}/audio/transcriptions`."""

    name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        language_hint: Sequence[str] | None = None,
    ) -> Transcript:
        wav = pcm_to_wav(pcm, sample_rate)
        data: dict[str, str] = {"model": self._model, "response_format": "verbose_json"}
        # Only pin the language when the role expects exactly one. Sending a hint for a
        # bilingual role would force Persian speech to be transcribed as English words,
        # which is worse than no hint at all.
        if language_hint and len(language_hint) == 1:
            data["language"] = language_hint[0]

        try:
            response = await get_client().post(
                f"{self._base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": ("utterance.wav", wav, "audio/wav")},
                data=data,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ProviderError(
                f"STT request failed: {describe_http_error(exc)}",
                category=ErrorCategory.STT_FAILED,
            ) from exc

        if not isinstance(payload, dict) or "text" not in payload:
            raise ProviderError(
                "STT response did not contain a 'text' field",
                category=ErrorCategory.STT_FAILED,
            )

        language = payload.get("language")
        return Transcript(
            text=str(payload["text"]).strip(),
            # Whisper reports full names ("persian"); normalise to the codes the roles
            # and prompts use. Anything unrecognised becomes None rather than a guess.
            language=_normalise_language(language),
            metadata={"provider": self.name, "model": self._model},
        )


#: Whisper's verbose_json reports language names, not codes.
_LANGUAGE_ALIASES = {
    "persian": "fa",
    "farsi": "fa",
    "fa": "fa",
    "english": "en",
    "en": "en",
}


def _normalise_language(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return _LANGUAGE_ALIASES.get(value.strip().lower())
