"""Provider adapters and the registry that selects them from configuration.

Import rule for the rest of the backend: import from `backend.providers.base` for types
and from `backend.providers.registry` for instances. Never import a vendor adapter
directly outside this package — that import is the coupling this whole layer exists to
prevent.
"""

from backend.providers.base import (
    AudioChunk,
    LLMProvider,
    LLMResult,
    STTProvider,
    Transcript,
    TTSProvider,
)

__all__ = [
    "AudioChunk",
    "LLMProvider",
    "LLMResult",
    "STTProvider",
    "TTSProvider",
    "Transcript",
]
