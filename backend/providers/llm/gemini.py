"""LLM against Google's Generative Language API.

Included as the second adapter specifically because Gemini's request shape is *not*
OpenAI-compatible: it uses `contents`/`parts`, carries the system prompt in a separate
`systemInstruction` field, and takes the key as a header rather than a bearer token.
Supporting it proves the abstraction is real rather than an OpenAI-shaped hole.

> **NOT LIVE-API VERIFIED.** Unit tested against a mocked transport only. The request
> and response shapes follow Google's published `generateContent` documentation.
"""

from __future__ import annotations

from typing import Any, Sequence

from backend.errors import ErrorCategory, ProviderError
from backend.providers.base import LLMResult
from backend.providers.http import describe_http_error, get_client


class GeminiLLM:
    """Translates the internal message list into Gemini's `generateContent` format."""

    name = "gemini"

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    @staticmethod
    def _to_gemini(messages: Sequence[dict[str, str]]) -> tuple[str, list[dict[str, Any]]]:
        """Split an OpenAI-style list into (system instruction, contents).

        Gemini has no `system` turn and names the assistant role `model`. Consecutive
        system messages are concatenated, because the API accepts only one.
        """
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                system_parts.append(content)
                continue
            contents.append(
                {"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]}
            )
        return "\n\n".join(system_parts), contents

    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_output_tokens: int = 300,
    ) -> LLMResult:
        system_instruction, contents = self._to_gemini(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        try:
            response = await get_client().post(
                f"{self._base_url}/models/{self._model}:generateContent",
                headers={
                    "x-goog-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ProviderError(
                f"Gemini request failed: {describe_http_error(exc)}",
                category=ErrorCategory.LLM_FAILED,
            ) from exc

        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            # A safety block produces a candidate with no parts. Surface it as a normal
            # provider failure so the pipeline's social fallback handles it.
            reason = ""
            if isinstance(payload, dict):
                candidates = payload.get("candidates") or [{}]
                reason = str(candidates[0].get("finishReason", "")) if candidates else ""
            raise ProviderError(
                f"Gemini response contained no text (finishReason={reason or 'unknown'})",
                category=ErrorCategory.LLM_FAILED,
            ) from exc

        return LLMResult(
            text=text,
            model=self._model,
            metadata={"provider": self.name, "usage": payload.get("usageMetadata")},
        )
