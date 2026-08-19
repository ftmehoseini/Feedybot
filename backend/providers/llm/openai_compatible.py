"""LLM against any OpenAI-compatible `/chat/completions` endpoint.

Covers OpenAI, Groq, OpenRouter, Together, vLLM, Ollama's compatibility endpoint, and
anything else that speaks the same shape — selected purely by `LLM_BASE_URL`.

> **NOT LIVE-API VERIFIED.** Unit tested against a mocked transport only.
"""

from __future__ import annotations

from typing import Sequence

from backend.errors import ErrorCategory, ProviderError
from backend.providers.base import LLMResult
from backend.providers.http import describe_http_error, get_client


class OpenAICompatibleLLM:
    """POST a chat message list, return the first choice's content."""

    name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_output_tokens: int = 300,
    ) -> LLMResult:
        body = {
            "model": self._model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        try:
            response = await get_client().post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ProviderError(
                f"LLM request failed: {describe_http_error(exc)}",
                category=ErrorCategory.LLM_FAILED,
            ) from exc

        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "LLM response did not contain choices[0].message.content",
                category=ErrorCategory.LLM_FAILED,
            ) from exc

        usage = payload.get("usage") if isinstance(payload, dict) else None
        return LLMResult(
            text=str(text),
            model=str(payload.get("model", self._model)),
            metadata={"provider": self.name, "usage": usage},
        )
