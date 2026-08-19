"""Configuration validation and provider selection.

Two properties: a misconfigured deployment fails at startup with a message naming the
setting, and swapping a provider is a configuration change with no code impact.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from backend.config import Settings
from backend.errors import ProviderError
from backend.logging_setup import configure_logging, log_event
from backend.providers.base import LLMProvider, STTProvider, TTSProvider
from backend.providers.registry import build_llm, build_providers, build_stt, build_tts

from .conftest import PROMPTS_DIR, ROLES_DIR


def base_settings(**overrides) -> Settings:
    return Settings(roles_dir=ROLES_DIR, prompts_dir=PROMPTS_DIR, **overrides)


def test_defaults_run_without_any_credentials() -> None:
    """The backend must start and hold a conversation with no API keys at all."""
    settings = base_settings()
    bundle = build_providers(settings)
    assert bundle.describe() == {"stt": "fake", "llm": "fake", "tts": "fake"}


@pytest.mark.parametrize(
    "overrides, missing",
    [
        ({"llm_provider": "openai_compatible"}, "LLM_API_KEY"),
        ({"llm_provider": "gemini"}, "GEMINI_API_KEY"),
        ({"stt_provider": "openai_compatible"}, "STT_API_KEY"),
        ({"tts_provider": "openai_compatible"}, "TTS_API_KEY"),
    ],
)
def test_a_real_provider_without_its_key_fails_at_startup(
    overrides: dict, missing: str
) -> None:
    """Discovering this when the first person speaks is the failure mode to avoid."""
    with pytest.raises(Exception) as excinfo:
        base_settings(**overrides)
    assert missing in str(excinfo.value)


def test_bad_directories_are_rejected() -> None:
    with pytest.raises(Exception, match="does not exist"):
        Settings(roles_dir="/nonexistent/roles", prompts_dir=PROMPTS_DIR)
    with pytest.raises(Exception, match="does not exist"):
        Settings(roles_dir=ROLES_DIR, prompts_dir="/nonexistent/prompts")


@pytest.mark.parametrize(
    "overrides",
    [
        {"port": 0},
        {"port": 99999},
        {"llm_temperature": 5.0},
        {"max_history_turns": 0},
        {"stt_timeout_s": 0},
        {"log_level": "CHATTY"},
        {"llm_provider": "some_startup_from_2027"},
    ],
)
def test_out_of_range_settings_are_rejected(overrides: dict) -> None:
    with pytest.raises(Exception):
        base_settings(**overrides)


def test_each_provider_slot_is_independently_selectable() -> None:
    """Mixing vendors must work: local STT, hosted LLM, another vendor's TTS."""
    settings = base_settings(
        stt_provider="openai_compatible",
        stt_api_key="k1",
        stt_base_url="http://localhost:9000/v1",
        llm_provider="gemini",
        gemini_api_key="k2",
        tts_provider="openai_compatible",
        tts_api_key="k3",
    )
    bundle = build_providers(settings)
    assert bundle.describe() == {
        "stt": "openai_compatible",
        "llm": "gemini",
        "tts": "openai_compatible",
    }


def test_every_adapter_satisfies_its_interface() -> None:
    settings = base_settings(
        stt_provider="openai_compatible", stt_api_key="k",
        llm_provider="openai_compatible", llm_api_key="k",
        tts_provider="openai_compatible", tts_api_key="k",
    )
    assert isinstance(build_stt(settings), STTProvider)
    assert isinstance(build_llm(settings), LLMProvider)
    assert isinstance(build_tts(settings), TTSProvider)
    assert isinstance(build_llm(base_settings(llm_provider="gemini", gemini_api_key="k")), LLMProvider)


# ---------------------------------------------------------------------------------------
# Adapter behaviour against a mocked transport. No network is used.
# ---------------------------------------------------------------------------------------


@pytest.fixture
def mock_transport(monkeypatch):
    """Replace the shared HTTP client with one backed by a scripted handler.

    Patches the module's cached client rather than `get_client`, because each adapter
    imported that function by name at import time and would keep the original binding.
    """

    def install(handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("backend.providers.http._client", client)
        return client

    return install


async def test_openai_llm_parses_a_normal_response(mock_transport) -> None:
    from backend.providers.llm.openai_compatible import OpenAICompatibleLLM

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "Hi!\n#emotion: happy"}}],
                "usage": {"total_tokens": 12},
            },
        )

    mock_transport(handler)
    provider = OpenAICompatibleLLM(base_url="http://x/v1", api_key="k", model="test-model")
    result = await provider.generate([{"role": "system", "content": "s"}])
    assert "Hi!" in result.text
    assert result.metadata["usage"]["total_tokens"] == 12


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="internal error"),
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(200, json={"unexpected": "shape"}),
        httpx.Response(200, json={"choices": []}),
    ],
)
async def test_openai_llm_turns_every_failure_into_a_provider_error(
    mock_transport, response: httpx.Response
) -> None:
    """The pipeline only knows how to handle ProviderError. Everything must become one."""
    from backend.providers.llm.openai_compatible import OpenAICompatibleLLM

    mock_transport(lambda request: response)
    provider = OpenAICompatibleLLM(base_url="http://x/v1", api_key="k", model="m")
    with pytest.raises(ProviderError):
        await provider.generate([{"role": "user", "content": "hi"}])


async def test_gemini_translates_the_message_shape(mock_transport) -> None:
    """Gemini is the proof the abstraction is real and not an OpenAI-shaped hole."""
    from backend.providers.llm.gemini import GeminiLLM

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        # The key must travel as a header, never in the URL where it would be logged.
        assert request.headers["x-goog-api-key"] == "k"
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "Salaam!"}]}}]}
        )

    mock_transport(handler)
    provider = GeminiLLM(base_url="http://x/v1beta", api_key="k", model="gemini-test")
    result = await provider.generate(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "again"},
        ]
    )
    assert result.text == "Salaam!"
    # System prompt moved out of `contents` into `systemInstruction`.
    assert captured["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert [c["role"] for c in captured["contents"]] == ["user", "model", "user"]


async def test_gemini_safety_block_becomes_a_provider_error(mock_transport) -> None:
    from backend.providers.llm.gemini import GeminiLLM

    mock_transport(
        lambda request: httpx.Response(
            200, json={"candidates": [{"finishReason": "SAFETY"}]}
        )
    )
    provider = GeminiLLM(base_url="http://x", api_key="k", model="m")
    with pytest.raises(ProviderError, match="SAFETY"):
        await provider.generate([{"role": "user", "content": "hi"}])


async def test_tts_rejects_a_sample_rate_the_robot_cannot_play(mock_transport) -> None:
    """The robot's I2S clock is fixed; silently wrong-rate audio plays chipmunked."""
    from backend.audio import pcm_to_wav, tone
    from backend.providers.tts.openai_compatible import OpenAICompatibleTTS

    mock_transport(
        lambda request: httpx.Response(200, content=pcm_to_wav(tone(100), 24000))
    )
    provider = OpenAICompatibleTTS(
        base_url="http://x/v1", api_key="k", model="m", default_voice="alloy"
    )
    with pytest.raises(ProviderError, match="24000 Hz"):
        [chunk async for chunk in provider.synthesize("hi", sample_rate=16000)]


async def test_stt_only_pins_a_language_for_a_monolingual_role(mock_transport) -> None:
    """Forcing a language on a bilingual role transcribes Persian as English words."""
    from backend.providers.stt.openai_compatible import OpenAICompatibleSTT

    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"text": "hello", "language": "english"})

    mock_transport(handler)
    provider = OpenAICompatibleSTT(base_url="http://x/v1", api_key="k", model="m")

    await provider.transcribe(b"\x00" * 320, sample_rate=16000, language_hint=("fa", "en"))
    assert b'name="language"' not in seen[-1]

    await provider.transcribe(b"\x00" * 320, sample_rate=16000, language_hint=("en",))
    assert b'name="language"' in seen[-1]


async def test_stt_normalises_language_names_to_codes(mock_transport) -> None:
    from backend.providers.stt.openai_compatible import OpenAICompatibleSTT

    mock_transport(
        lambda request: httpx.Response(200, json={"text": "سلام", "language": "persian"})
    )
    provider = OpenAICompatibleSTT(base_url="http://x/v1", api_key="k", model="m")
    transcript = await provider.transcribe(b"\x00" * 320, sample_rate=16000)
    assert transcript.language == "fa"


async def test_stt_reports_no_language_rather_than_guessing(mock_transport) -> None:
    from backend.providers.stt.openai_compatible import OpenAICompatibleSTT

    mock_transport(
        lambda request: httpx.Response(200, json={"text": "hola", "language": "spanish"})
    )
    provider = OpenAICompatibleSTT(base_url="http://x/v1", api_key="k", model="m")
    transcript = await provider.transcribe(b"\x00" * 320, sample_rate=16000)
    assert transcript.language is None


# ---------------------------------------------------------------------------------------
# Logging policy
# ---------------------------------------------------------------------------------------


def test_secrets_are_redacted_from_logs(capsys) -> None:
    configure_logging("INFO", log_transcripts=False)
    log_event(
        logging.getLogger("test"),
        logging.INFO,
        "provider call",
        api_key="sk-do-not-log-me",
        nested={"authorization": "Bearer do-not-log-me"},
        latency_ms=42,
    )
    output = capsys.readouterr().out
    assert "do-not-log-me" not in output
    assert "[redacted]" in output
    assert '"latency_ms": 42' in output


def test_transcripts_are_withheld_by_default(capsys) -> None:
    """What a person says to a robot in their home is not routine telemetry."""
    configure_logging("INFO", log_transcripts=False)
    log_event(logging.getLogger("test"), logging.INFO, "turn", text="something private")
    output = capsys.readouterr().out
    assert "something private" not in output
    # The length survives: useful for debugging, revealing nothing.
    assert "text_len" in output


def test_transcripts_can_be_enabled_for_development(capsys) -> None:
    configure_logging("INFO", log_transcripts=True)
    log_event(logging.getLogger("test"), logging.INFO, "turn", text="debugging this")
    assert "debugging this" in capsys.readouterr().out
    configure_logging("INFO", log_transcripts=False)
