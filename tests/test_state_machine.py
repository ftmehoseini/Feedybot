"""Session state machine and history bounds."""

from __future__ import annotations

import pytest

from backend.emotion import SystemState
from backend.session import RobotSession, is_valid_transition
from backend.roles.schema import RolePack


@pytest.mark.parametrize(
    "current, target",
    [
        (SystemState.OFFLINE, SystemState.IDLE),
        (SystemState.IDLE, SystemState.LISTENING),
        (SystemState.LISTENING, SystemState.PROCESSING),
        (SystemState.PROCESSING, SystemState.THINKING),
        (SystemState.THINKING, SystemState.SPEAKING),
        (SystemState.SPEAKING, SystemState.LISTENING),
        (SystemState.ERROR, SystemState.LISTENING),
        (SystemState.THINKING, SystemState.ERROR),
        (SystemState.SPEAKING, SystemState.OFFLINE),
    ],
)
def test_valid_transitions_are_allowed(current: SystemState, target: SystemState) -> None:
    assert is_valid_transition(current, target)


@pytest.mark.parametrize(
    "current, target, why",
    [
        (SystemState.LISTENING, SystemState.SPEAKING, "must pass through processing"),
        (SystemState.IDLE, SystemState.THINKING, "cannot think without hearing"),
        (SystemState.OFFLINE, SystemState.SPEAKING, "cannot speak while disconnected"),
        (SystemState.OFFLINE, SystemState.LISTENING, "must connect first"),
        (SystemState.ERROR, SystemState.SPEAKING, "must recover before speaking"),
    ],
)
def test_invalid_transitions_are_rejected(
    current: SystemState, target: SystemState, why: str
) -> None:
    assert not is_valid_transition(current, target), why


def test_rejected_transition_leaves_state_untouched(session: RobotSession) -> None:
    session.force_state(SystemState.LISTENING)
    assert not session.transition(SystemState.SPEAKING)
    assert session.state is SystemState.LISTENING


def test_every_state_can_reach_offline() -> None:
    """Disconnection can happen at any moment and must always be representable."""
    for state in SystemState:
        assert is_valid_transition(state, SystemState.OFFLINE)


def test_error_always_recovers_to_a_usable_state() -> None:
    """The core reliability promise: ERROR is never terminal."""
    assert is_valid_transition(SystemState.ERROR, SystemState.IDLE)
    assert is_valid_transition(SystemState.ERROR, SystemState.LISTENING)


def test_force_state_bypasses_validation_for_recovery(session: RobotSession) -> None:
    session.force_state(SystemState.THINKING)
    session.force_state(SystemState.LISTENING)
    assert session.state is SystemState.LISTENING


def test_turn_ids_are_monotonic(session: RobotSession) -> None:
    assert session.begin_turn() == 1
    assert session.begin_turn() == 2
    assert session.begin_turn() == 3


def test_a_stale_turn_id_is_not_current(session: RobotSession) -> None:
    """A late result from an abandoned turn must not talk over the current one."""
    first = session.begin_turn()
    session.begin_turn()
    assert not session.is_current_turn(first)


def test_cancellation_invalidates_the_current_turn(session: RobotSession) -> None:
    turn = session.begin_turn()
    assert session.is_current_turn(turn)
    session.cancel_turn()
    assert not session.is_current_turn(turn)
    assert session.cancelled


def test_beginning_a_turn_clears_a_previous_cancellation(session: RobotSession) -> None:
    session.begin_turn()
    session.cancel_turn()
    turn = session.begin_turn()
    assert not session.cancelled
    assert session.is_current_turn(turn)


def test_history_is_bounded_by_turn_count(session: RobotSession) -> None:
    for index in range(20):
        session.record_exchange(f"user {index}", f"robot {index}")
    session.trim_history(max_turns=5, max_chars=100_000)
    assert session.turn_count == 5
    # Oldest dropped first, so the most recent context survives.
    assert session.history[0]["content"] == "user 15"


def test_history_is_bounded_by_character_budget(session: RobotSession) -> None:
    for index in range(20):
        session.record_exchange("x" * 500, "y" * 500)
    session.trim_history(max_turns=100, max_chars=3000)
    total = sum(len(m["content"]) for m in session.history)
    assert total <= 3000


def test_history_always_starts_with_a_user_turn_after_trimming(
    session: RobotSession,
) -> None:
    """Trimming in pairs keeps the transcript coherent for the model."""
    for index in range(20):
        session.record_exchange(f"u{index}", f"a{index}")
    session.trim_history(max_turns=3, max_chars=100_000)
    assert session.history[0]["role"] == "user"
    assert session.history[-1]["role"] == "assistant"


def test_reconnect_resets_the_conversation(session: RobotSession) -> None:
    """After a gap the previous turn is stale; resuming it mid-thought is worse."""
    session.record_exchange("hello", "hi there")
    session.begin_turn()
    session.reset_conversation()
    assert session.history == []
    assert session.turn_id == 0
    assert session.metrics is None
    assert not session.cancelled


def test_sessions_do_not_share_history(companion_role: RolePack) -> None:
    """No global conversation state: two robots must not hear each other."""
    first = RobotSession(role=companion_role, device_id="a")
    second = RobotSession(role=companion_role, device_id="b")
    first.record_exchange("private", "reply")
    assert second.history == []
    assert first.session_id != second.session_id


def test_idle_expiry_uses_the_configured_timeout(session: RobotSession) -> None:
    assert not session.is_idle_expired(timeout_s=1000)
    assert session.is_idle_expired(timeout_s=-1)


def test_language_hint_comes_from_the_role(
    companion_role: RolePack, teacher_role: RolePack
) -> None:
    companion = RobotSession(role=companion_role)
    teacher = RobotSession(role=teacher_role)
    assert set(companion.language_hint()) == {"fa", "en"}
    assert set(teacher.language_hint()) == {"fa", "en"}
