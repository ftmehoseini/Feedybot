"""Per-connection conversation state.

One `RobotSession` per connected robot. There is no module-level conversation state
anywhere in the backend: V1 ships one robot, but a global history would make the second
one a rewrite rather than a deployment.

The session also owns the state machine. Transitions are validated against an explicit
table, because "how did it get stuck in THINKING?" is a question you can only answer if
illegal transitions are rejected at the point they are attempted.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable

from backend.emotion import Expression, SystemState
from backend.logging_setup import log_event
from backend.roles.schema import RolePack

logger = logging.getLogger(__name__)

#: Legal system-state transitions. Anything not listed is a bug, and rejecting it here
#: turns a silent stuck state into a loud log line.
_ALLOWED_TRANSITIONS: dict[SystemState, frozenset[SystemState]] = {
    SystemState.OFFLINE: frozenset({SystemState.IDLE, SystemState.OFFLINE}),
    SystemState.IDLE: frozenset(
        {SystemState.LISTENING, SystemState.PROCESSING, SystemState.SPEAKING,
         SystemState.ERROR, SystemState.OFFLINE, SystemState.IDLE}
    ),
    SystemState.LISTENING: frozenset(
        {SystemState.PROCESSING, SystemState.IDLE, SystemState.ERROR,
         SystemState.OFFLINE, SystemState.LISTENING}
    ),
    SystemState.PROCESSING: frozenset(
        {SystemState.THINKING, SystemState.LISTENING, SystemState.IDLE,
         SystemState.ERROR, SystemState.OFFLINE, SystemState.PROCESSING}
    ),
    SystemState.THINKING: frozenset(
        {SystemState.SPEAKING, SystemState.LISTENING, SystemState.IDLE,
         SystemState.ERROR, SystemState.OFFLINE, SystemState.THINKING}
    ),
    SystemState.SPEAKING: frozenset(
        {SystemState.LISTENING, SystemState.IDLE, SystemState.ERROR,
         SystemState.OFFLINE, SystemState.SPEAKING}
    ),
    # ERROR is always recoverable: the whole point is that the robot comes back.
    SystemState.ERROR: frozenset(
        {SystemState.IDLE, SystemState.LISTENING, SystemState.OFFLINE, SystemState.ERROR}
    ),
}


def is_valid_transition(current: SystemState, target: SystemState) -> bool:
    """Whether moving from `current` to `target` is allowed."""
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


@dataclass
class TurnMetrics:
    """Latency breakdown for one conversational turn.

    Measured, not estimated. The point is to know *where* the seconds go before anyone
    starts optimising: in practice the answer is usually TTS, and people usually guess
    the LLM.
    """

    turn_id: int
    utterance_end_at: float = field(default_factory=time.monotonic)
    stt_done_at: float | None = None
    llm_done_at: float | None = None
    tts_first_chunk_at: float | None = None
    first_audio_sent_at: float | None = None
    playback_done_at: float | None = None
    audio_bytes: int = 0

    @staticmethod
    def _ms(start: float, end: float | None) -> int | None:
        return None if end is None else int((end - start) * 1000)

    def summary(self) -> dict[str, int | None]:
        """The four numbers worth logging per turn, in milliseconds."""
        return {
            "stt_ms": self._ms(self.utterance_end_at, self.stt_done_at),
            "llm_ms": None
            if self.stt_done_at is None
            else self._ms(self.stt_done_at, self.llm_done_at),
            "tts_first_audio_ms": None
            if self.llm_done_at is None
            else self._ms(self.llm_done_at, self.tts_first_chunk_at),
            # The number the human actually experiences: they stopped talking, and this
            # is how long the silence lasted.
            "time_to_first_audio_ms": self._ms(self.utterance_end_at, self.first_audio_sent_at),
            # Only non-null after the robot confirms playback finished; the pipeline
            # returns before that, so this is filled in by the connection layer.
            "total_ms": self._ms(self.utterance_end_at, self.playback_done_at),
            "audio_bytes": self.audio_bytes,
        }


@dataclass
class RobotSession:
    """Everything the backend knows about one connected robot."""

    role: RolePack
    device_id: str = "unknown"
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: SystemState = SystemState.OFFLINE
    #: Monotonic within a session. A result tagged with a stale turn id is discarded,
    #: which is how a cancelled turn's late TTS cannot talk over the next one.
    turn_id: int = 0
    pending_expression: Expression | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_activity_at: float = field(default_factory=time.monotonic)
    #: Serialises turns. Two utterances arriving at once must not interleave pipelines.
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: Set when the active turn should abandon its work.
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    metrics: TurnMetrics | None = None

    #: [{"role": "user"|"assistant", "content": str}], oldest first.
    _history: list[dict[str, str]] = field(default_factory=list)

    # -- state machine ---------------------------------------------------------------

    def transition(self, target: SystemState) -> bool:
        """Attempt a state transition. Returns False (and logs) if it is not legal."""
        if not is_valid_transition(self.state, target):
            log_event(
                logger,
                logging.WARNING,
                "rejected illegal state transition",
                session_id=self.session_id,
                from_state=self.state.value,
                to_state=target.value,
            )
            return False
        self.state = target
        self.touch()
        return True

    def force_state(self, target: SystemState) -> None:
        """Set state without validation. Only for recovery paths (disconnect, reset).

        Kept separate from `transition` so that "we are giving up and resetting" is
        visible in the code rather than hidden behind a permissive transition table.
        """
        self.state = target
        self.touch()

    def touch(self) -> None:
        """Mark the session as recently active, for idle-timeout purposes."""
        self.last_activity_at = time.monotonic()

    def is_idle_expired(self, timeout_s: float) -> bool:
        return (time.monotonic() - self.last_activity_at) > timeout_s

    # -- turns ------------------------------------------------------------------------

    def begin_turn(self) -> int:
        """Allocate the next turn id and reset per-turn state."""
        self.turn_id += 1
        self.cancel_event = asyncio.Event()
        self.metrics = TurnMetrics(turn_id=self.turn_id)
        self.touch()
        return self.turn_id

    def cancel_turn(self) -> None:
        """Signal the active turn to stop. Safe to call when no turn is running."""
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def is_current_turn(self, turn_id: int) -> bool:
        """Whether `turn_id` is still the live turn. Late results fail this check."""
        return turn_id == self.turn_id and not self.cancelled

    # -- history ----------------------------------------------------------------------

    @property
    def history(self) -> list[dict[str, str]]:
        """The bounded conversation history, oldest first."""
        return list(self._history)

    @property
    def turn_count(self) -> int:
        """Completed exchanges so far."""
        return len(self._history) // 2

    def record_exchange(self, user_text: str, assistant_text: str) -> None:
        """Append one exchange and re-apply the bounds."""
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": assistant_text})
        self.touch()

    def trim_history(self, *, max_turns: int, max_chars: int) -> None:
        """Enforce both history bounds, dropping oldest exchanges first.

        Truncation, not summarisation: V1 has no memory system, and a summariser is an
        extra LLM call per turn that would need its own timeout, failure mode and
        budget. Dropping the oldest exchange is honest and free.
        """
        while self.turn_count > max_turns:
            del self._history[0:2]
        while self._history and sum(len(m["content"]) for m in self._history) > max_chars:
            del self._history[0:2]

    def reset_conversation(self) -> None:
        """Clear history and turn state.

        Called on reconnect: after a network gap the previous turn's context is stale
        (the human has moved on, or walked away), and resuming it mid-thought is worse
        than starting clean.
        """
        self._history.clear()
        self.turn_id = 0
        self.metrics = None
        self.pending_expression = None
        self.cancel_event = asyncio.Event()
        self.touch()

    # -- role ---------------------------------------------------------------------------

    def language_hint(self) -> Iterable[str]:
        """Languages to hint to STT, best-first, according to the active role."""
        return tuple(self.role.languages.primary)
