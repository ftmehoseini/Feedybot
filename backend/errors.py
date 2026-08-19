"""Typed error categories for the conversation pipeline.

Errors carry two things the rest of the system needs to keep separate: a technical
category for logs and metrics, and a *social* consequence for the human standing in
front of the robot. A person should never hear a status code.
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    """What went wrong, in terms the pipeline and metrics care about."""

    STT_FAILED = "stt_failed"
    STT_TIMEOUT = "stt_timeout"
    STT_EMPTY = "stt_empty"
    LLM_FAILED = "llm_failed"
    LLM_TIMEOUT = "llm_timeout"
    LLM_EMPTY = "llm_empty"
    TTS_FAILED = "tts_failed"
    TTS_TIMEOUT = "tts_timeout"
    CANCELLED = "cancelled"
    PROTOCOL = "protocol"
    CONFIG = "config"
    INTERNAL = "internal"


class FafobotError(Exception):
    """Base class for errors that the pipeline knows how to turn into behaviour."""

    category: ErrorCategory = ErrorCategory.INTERNAL

    def __init__(self, message: str, *, category: ErrorCategory | None = None) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category


class ProviderError(FafobotError):
    """A provider (STT/LLM/TTS) failed or misbehaved."""


class ProviderTimeout(ProviderError):
    """A provider did not answer inside its configured budget."""


class ConfigError(FafobotError):
    """Configuration is invalid. Raised at startup, never mid-conversation."""

    category = ErrorCategory.CONFIG


class ProtocolError(FafobotError):
    """A peer sent something the protocol does not allow."""

    category = ErrorCategory.PROTOCOL


class TurnCancelled(FafobotError):
    """The active turn was cancelled (long press, disconnect, or a newer turn)."""

    category = ErrorCategory.CANCELLED
