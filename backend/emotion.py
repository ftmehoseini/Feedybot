"""The two orthogonal axes of what the robot's face shows.

These are deliberately *different types*. `SystemState` is where the machine is in the
conversation; `Expression` is what the character feels. Mixing them is how robots end
up "looking thoughtful" because the network is slow. The face composes both — mouth
from state, eyes from expression — but the two never collapse into one enum.
"""

from __future__ import annotations

from enum import Enum


class SystemState(str, Enum):
    """Where the machine is. Driven by the pipeline, never by the model."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"  # audio received, STT running
    THINKING = "thinking"      # LLM running
    SPEAKING = "speaking"
    ERROR = "error"
    OFFLINE = "offline"


class Expression(str, Enum):
    """What the character feels. Chosen by the role/model, always validated."""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    CURIOUS = "curious"
    CONFUSED = "confused"
    ENCOURAGING = "encouraging"
    SURPRISED = "surprised"
    SLEEPY = "sleepy"


#: Expression used whenever the model gives us something we do not recognise. Falling
#: back to neutral is always safe; guessing is not.
DEFAULT_EXPRESSION = Expression.NEUTRAL

#: How long a *reactive* expression holds on the device before it decays to the role's
#: resting face. The device enforces this locally so a dropped packet cannot freeze a
#: grin permanently (see docs/ARCHITECTURE.md#local-face-recovery).
DEFAULT_EXPRESSION_HOLD_MS = 1800


def parse_expression(value: str | None) -> Expression:
    """Coerce arbitrary text into a known expression, defaulting to neutral.

    Accepts case and whitespace variation because this parses model output, which is
    only ever *mostly* well-behaved.
    """
    if not value:
        return DEFAULT_EXPRESSION
    try:
        return Expression(value.strip().lower())
    except ValueError:
        return DEFAULT_EXPRESSION
