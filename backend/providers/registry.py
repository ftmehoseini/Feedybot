"""Selecting concrete providers from configuration.

This is the only module in the backend allowed to import a vendor adapter. Everything
else receives providers as constructor arguments. That single rule is what makes
"replace Gemini with Ollama" a configuration change rather than a refactor.

Dependency injection here is a function returning a dataclass — no container, no
registry decorators, no plugin discovery. A one-robot product does not need more.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.config import Settings
from backend.errors import ConfigError
from backend.providers.base import LLMProvider, STTProvider, TTSProvider


@dataclass(frozen=True)
class ProviderBundle:
    """The three providers one backend instance runs with."""

    stt: STTProvider
    llm: LLMProvider
    tts: TTSProvider

    def describe(self) -> dict[str, str]:
        """Provider names, for the startup banner and structured logs."""
        return {"stt": self.stt.name, "llm": self.llm.name, "tts": self.tts.name}


def build_stt(settings: Settings) -> STTProvider:
    """Instantiate the configured STT provider."""
    if settings.stt_provider == "fake":
        from backend.providers.stt.fake import FakeSTTProvider

        return FakeSTTProvider()
    if settings.stt_provider == "openai_compatible":
        from backend.providers.stt.openai_compatible import OpenAICompatibleSTT

        assert settings.stt_api_key  # guaranteed by Settings validation
        return OpenAICompatibleSTT(
            base_url=settings.stt_base_url,
            api_key=settings.stt_api_key,
            model=settings.stt_model,
        )
    raise ConfigError(f"unknown STT_PROVIDER: {settings.stt_provider}")


def build_llm(settings: Settings) -> LLMProvider:
    """Instantiate the configured LLM provider."""
    if settings.llm_provider == "fake":
        from backend.providers.llm.fake import FakeLLMProvider

        return FakeLLMProvider()
    if settings.llm_provider == "openai_compatible":
        from backend.providers.llm.openai_compatible import OpenAICompatibleLLM

        assert settings.llm_api_key
        return OpenAICompatibleLLM(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )
    if settings.llm_provider == "gemini":
        from backend.providers.llm.gemini import GeminiLLM

        assert settings.gemini_api_key
        return GeminiLLM(
            base_url=settings.gemini_base_url,
            api_key=settings.gemini_api_key,
            model=settings.llm_model,
        )
    raise ConfigError(f"unknown LLM_PROVIDER: {settings.llm_provider}")


def build_tts(settings: Settings) -> TTSProvider:
    """Instantiate the configured TTS provider."""
    if settings.tts_provider == "fake":
        from backend.providers.tts.fake import FakeTTSProvider

        return FakeTTSProvider()
    if settings.tts_provider == "openai_compatible":
        from backend.providers.tts.openai_compatible import OpenAICompatibleTTS

        assert settings.tts_api_key
        return OpenAICompatibleTTS(
            base_url=settings.tts_base_url,
            api_key=settings.tts_api_key,
            model=settings.tts_model,
            default_voice=settings.tts_voice,
        )
    raise ConfigError(f"unknown TTS_PROVIDER: {settings.tts_provider}")


def build_providers(settings: Settings) -> ProviderBundle:
    """Build all three providers for the given settings."""
    return ProviderBundle(stt=build_stt(settings), llm=build_llm(settings), tts=build_tts(settings))
