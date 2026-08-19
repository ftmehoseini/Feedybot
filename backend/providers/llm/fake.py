"""In-process LLM for tests, the simulator, and key-less local runs.

It echoes enough of its input that tests can assert on prompt composition through the
public surface, and it emits a real `#emotion:` marker so the parsing path is exercised
end to end rather than bypassed.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Sequence

from backend.errors import ErrorCategory, ProviderError
from backend.providers.base import LLMResult


class FakeLLMProvider:
    """Returns queued replies in order, then a generated acknowledgement.

    Args:
        script: raw model outputs to return in order. Include the emotion marker
            yourself if you want to test a specific expression.
        fail_after: raise `ProviderError` from this call index onward.
        delay_s: artificial latency, for timeout tests.
        default_emotion: marker appended to generated (unscripted) replies.
    """

    name = "fake"

    def __init__(
        self,
        script: Sequence[str] | None = None,
        *,
        fail_after: int | None = None,
        delay_s: float = 0.0,
        default_emotion: str = "happy",
    ) -> None:
        self._script: deque[str] = deque(script or [])
        self._fail_after = fail_after
        self._delay_s = delay_s
        self._default_emotion = default_emotion
        self.calls = 0
        #: The last message list received. Tests assert prompt layering against this.
        self.last_messages: list[dict[str, str]] = []

    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_output_tokens: int = 300,
    ) -> LLMResult:
        index = self.calls
        self.calls += 1
        self.last_messages = [dict(m) for m in messages]
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._fail_after is not None and index >= self._fail_after:
            raise ProviderError("fake LLM failure", category=ErrorCategory.LLM_FAILED)
        if self._script:
            return LLMResult(text=self._script.popleft(), model="fake", metadata={"scripted": True})

        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        return LLMResult(
            text=f"You said: {last_user}\n#emotion: {self._default_emotion}",
            model="fake",
            metadata={"scripted": False},
        )
