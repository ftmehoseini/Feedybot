"""Structured logging with secret redaction.

Logs are JSON lines so that a fleet deployment can ship them somewhere without a parser.
Two policies are enforced here rather than left to call sites, because call sites forget:

- **Anything that looks like a secret is redacted**, whatever the caller passed.
- **Conversation content is dropped unless `LOG_TRANSCRIPTS=true`.** What a person says
  to a desk robot in their home is not routine telemetry.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

#: Field names whose values never reach the log, regardless of context.
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|credential)", re.IGNORECASE
)

#: Fields carrying conversation content, gated on LOG_TRANSCRIPTS.
_TRANSCRIPT_KEYS = frozenset({"text", "transcript", "speech", "user_text", "reply", "prompt"})

_REDACTED = "[redacted]"

#: Set by `configure_logging`. Module-level because the filter runs on every record and
#: threading settings through logging's API is worse than one module constant.
_log_transcripts = False


class _JsonFormatter(logging.Formatter):
    """Render each record as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload.update(_sanitise(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _sanitise(fields: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets and, unless enabled, conversation content."""
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if _SECRET_KEY_PATTERN.search(key):
            clean[key] = _REDACTED
        elif key in _TRANSCRIPT_KEYS and not _log_transcripts:
            # Keep the length: it is useful for debugging and reveals nothing.
            clean[f"{key}_len"] = len(value) if isinstance(value, (str, bytes)) else None
        elif isinstance(value, dict):
            clean[key] = _sanitise(value)
        else:
            clean[key] = value
    return clean


def configure_logging(level: str = "INFO", *, log_transcripts: bool = False) -> None:
    """Install the JSON formatter on the root logger. Idempotent."""
    global _log_transcripts
    _log_transcripts = log_transcripts

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers; make them go through ours so the output stream
    # stays uniformly parseable.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def log_event(
    logger: logging.Logger, level: int, message: str, /, **context: Any
) -> None:
    """Log `message` with structured `context`.

    Wrapper rather than direct `logger.info(..., extra=...)` so that every structured
    field passes through `_sanitise` exactly once.
    """
    logger.log(level, message, extra={"context": context})
