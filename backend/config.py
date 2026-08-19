"""Configuration and startup validation.

Everything the deployment can change lives here, and every secret arrives through the
environment. Bad configuration must fail loudly at startup with a message that names the
offending setting — never as a confusing `None` three layers into a provider call.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROLES_DIR = REPO_ROOT / "roles"
DEFAULT_PROMPTS_DIR = REPO_ROOT / "backend" / "prompts"


class Settings(BaseSettings):
    """Validated application settings, loaded from environment and `.env`.

    Field names map to upper-case environment variables (`llm_provider` ->
    `LLM_PROVIDER`). See `.env.example` for the documented set.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    # -- server ------------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    #: Off by default. Conversation content is private; turning this on writes user
    #: speech and model replies into the logs, which is a development-only choice.
    log_transcripts: bool = False

    # -- role ---------------------------------------------------------------------
    #: The deployment's personality. Changing this is the entire "reconfigure the
    #: robot" story — no firmware change, no code change.
    role_id: str = "social_companion"
    roles_dir: Path = DEFAULT_ROLES_DIR
    prompts_dir: Path = DEFAULT_PROMPTS_DIR

    # -- deployment identity ------------------------------------------------------
    robot_name: str = "Fafobot"
    deployment_venue: str = ""
    deployment_notes: str = ""

    # -- providers ----------------------------------------------------------------
    stt_provider: Literal["fake", "openai_compatible"] = "fake"
    llm_provider: Literal["fake", "openai_compatible", "gemini"] = "fake"
    tts_provider: Literal["fake", "openai_compatible"] = "fake"

    stt_model: str = "whisper-1"
    stt_base_url: str = "https://api.openai.com/v1"
    stt_api_key: str | None = None

    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    llm_max_output_tokens: int = Field(default=300, ge=32, le=4096)

    tts_model: str = "tts-1"
    tts_base_url: str = "https://api.openai.com/v1"
    tts_api_key: str | None = None
    tts_voice: str = "alloy"

    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # -- timeouts (seconds) --------------------------------------------------------
    # Every AI call has a budget. Exceeding it is a normal, handled outcome that
    # returns the robot to a usable state -- never a hang.
    stt_timeout_s: float = Field(default=15.0, gt=0)
    llm_timeout_s: float = Field(default=20.0, gt=0)
    tts_timeout_s: float = Field(default=20.0, gt=0)
    #: How long we wait for `playback_done` beyond the audio's own duration before
    #: assuming the device dropped the message and forcing the state machine onward.
    playback_grace_s: float = Field(default=10.0, gt=0)
    #: Idle time after which a session is considered stale and reset.
    session_idle_timeout_s: float = Field(default=900.0, gt=0)

    # -- conversation --------------------------------------------------------------
    #: One "turn" is a user message plus the robot's reply. Bounded so a long session
    #: cannot grow the prompt without limit.
    max_history_turns: int = Field(default=12, ge=1, le=200)
    #: Belt-and-braces cap alongside the turn count, for unusually long turns.
    max_history_chars: int = Field(default=8000, ge=500)

    @field_validator("roles_dir", "prompts_dir")
    @classmethod
    def _must_exist(cls, value: Path) -> Path:
        resolved = value.expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"directory does not exist: {resolved}")
        return resolved

    @model_validator(mode="after")
    def _require_keys_for_real_providers(self) -> "Settings":
        """Fail at startup if a real provider was selected without its credentials.

        The alternative -- discovering the missing key when the first human speaks --
        is exactly the obscure late crash this project is meant to avoid.
        """
        missing: list[str] = []
        if self.stt_provider == "openai_compatible" and not self.stt_api_key:
            missing.append("STT_API_KEY (required by STT_PROVIDER=openai_compatible)")
        if self.llm_provider == "openai_compatible" and not self.llm_api_key:
            missing.append("LLM_API_KEY (required by LLM_PROVIDER=openai_compatible)")
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            missing.append("GEMINI_API_KEY (required by LLM_PROVIDER=gemini)")
        if self.tts_provider == "openai_compatible" and not self.tts_api_key:
            missing.append("TTS_API_KEY (required by TTS_PROVIDER=openai_compatible)")
        if missing:
            raise ValueError("missing required configuration: " + "; ".join(missing))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process, translating validation errors into ConfigError."""
    try:
        return Settings()
    except Exception as exc:  # pydantic ValidationError or ValueError
        raise ConfigError(f"invalid configuration: {exc}") from exc
