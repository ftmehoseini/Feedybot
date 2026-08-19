"""The typed boundary between "model output" and "robot behaviour".

Raw model text stops here. Everything downstream — TTS, the face, the logs — works with
an `AgentReply`, never with a tagged string. That isolation is the point: model output is
untrusted, occasionally malformed, and always somebody else's format.

Three invariants this module guarantees to its callers:

1. An emotion marker is **never** returned in `speech`, so TTS can never read one aloud.
2. An unrecognised emotion becomes `NEUTRAL` rather than an exception.
3. An empty or whitespace-only reply is reported as such, so the pipeline can substitute
   a social fallback rather than sending silence to TTS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.emotion import DEFAULT_EXPRESSION, Expression, parse_expression

#: The marker convention the prompt asks models to use: `#emotion: happy` on its own
#: line. Tolerant by design — leading whitespace, any case, optional space after the
#: colon, and both `#emotion` and `emotion:` bare forms are accepted, because models
#: drift and a drifted marker must not become spoken words.
_EMOTION_MARKER = re.compile(
    r"^[ \t]*#?\s*emotion\s*[:=]\s*([A-Za-z_]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

#: Inline variant, e.g. a trailing `(#emotion: curious)` the model appended to a
#: sentence. Stripped too, for the same reason.
_INLINE_EMOTION_MARKER = re.compile(
    r"[\(\[]?\s*#\s*emotion\s*[:=]\s*([A-Za-z_]+)\s*[\)\]]?",
    re.IGNORECASE,
)

#: Markdown decorations that are meaningless when spoken. Removed rather than left for
#: the TTS engine to pronounce as "asterisk".
_MARKDOWN_NOISE = re.compile(r"(\*\*|__|\*|`|#{1,6}\s)")


@dataclass(frozen=True)
class AgentReply:
    """What the robot will say, and how it will look while saying it."""

    speech: str
    emotion: Expression = DEFAULT_EXPRESSION
    #: Free-form, non-authoritative annotations (provider, model, token counts).
    #: Deliberately not a typed schema: nothing branches on it in V1.
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """True when there is nothing worth sending to TTS."""
        return not self.speech.strip()


def parse_agent_output(raw: str | None, *, strip_markdown: bool = True) -> AgentReply:
    """Turn raw model text into a typed reply.

    Args:
        raw: whatever the LLM produced, including `None`.
        strip_markdown: remove markdown decorations that would be spoken literally.
            The core prompt already asks models not to emit them; this is the
            belt-and-braces pass for when they do anyway.

    Returns:
        An `AgentReply`. Never raises — malformed output degrades to neutral speech, and
        completely unusable output produces an empty reply the pipeline can detect via
        `is_empty`.
    """
    if not raw:
        return AgentReply(speech="", emotion=DEFAULT_EXPRESSION)

    text = raw
    emotion_token: str | None = None

    # Own-line markers first: they are the documented form, and taking them out early
    # keeps the inline pass from mangling ordinary prose containing the word "emotion".
    line_match = _EMOTION_MARKER.search(text)
    if line_match:
        emotion_token = line_match.group(1)
        text = _EMOTION_MARKER.sub("", text)

    inline_match = _INLINE_EMOTION_MARKER.search(text)
    if inline_match:
        emotion_token = emotion_token or inline_match.group(1)
        text = _INLINE_EMOTION_MARKER.sub("", text)

    if strip_markdown:
        text = _MARKDOWN_NOISE.sub("", text)

    # Collapse the blank lines left behind by marker removal, but keep intentional
    # sentence structure: TTS engines use punctuation for prosody.
    text = re.sub(r"\n{2,}", "\n", text).strip()

    return AgentReply(speech=text, emotion=parse_expression(emotion_token))
