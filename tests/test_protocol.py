"""Wire protocol tests.

The governing rule: **a malformed or unknown frame must never crash either side.** A
robot running newer firmware than its backend should degrade, not disconnect.
"""

from __future__ import annotations

import json

import pytest

from backend.emotion import Expression, SystemState
from backend.protocol import (
    AUDIO_BITS_PER_SAMPLE,
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    MAX_TEXT_FRAME_BYTES,
    MAX_UTTERANCE_BYTES,
    PROTOCOL_VERSION,
    CancelMessage,
    ExpressionMessage,
    HelloMessage,
    InteractionMessage,
    PlaybackDoneMessage,
    SpeakStartMessage,
    StateMessage,
    UnknownMessage,
    UtteranceEndMessage,
    UtteranceStartMessage,
    decode_client_message,
    encode,
)


def test_hello_round_trips() -> None:
    message = decode_client_message(
        json.dumps({"type": "hello", "protocol_version": 1, "device_id": "unit-1"})
    )
    assert isinstance(message, HelloMessage)
    assert message.device_id == "unit-1"
    assert message.capabilities.sample_rate == AUDIO_SAMPLE_RATE


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"type": "utterance_start"}, UtteranceStartMessage),
        ({"type": "utterance_end", "sample_count": 1000}, UtteranceEndMessage),
        ({"type": "cancel"}, CancelMessage),
        ({"type": "playback_done", "turn_id": 3}, PlaybackDoneMessage),
        ({"type": "interaction", "event": "short_touch"}, InteractionMessage),
        ({"type": "interaction", "event": "long_touch"}, InteractionMessage),
    ],
)
def test_every_client_message_decodes(payload: dict, expected: type) -> None:
    assert isinstance(decode_client_message(json.dumps(payload)), expected)


@pytest.mark.parametrize(
    "raw, reason",
    [
        ("not json at all", "invalid_json"),
        ("[1, 2, 3]", "not_an_object"),
        ('"a string"', "not_an_object"),
        ("{}", "missing_type"),
        ('{"type": 42}', "missing_type"),
        ('{"type": "from_the_future"}', "unknown_type"),
        ('{"type": "interaction", "event": "headbutt"}', "invalid_fields"),
        ('{"type": "hello"}', "invalid_fields"),
    ],
)
def test_bad_frames_degrade_instead_of_raising(raw: str, reason: str) -> None:
    message = decode_client_message(raw)
    assert isinstance(message, UnknownMessage)
    assert message.reason == reason


def test_oversized_frames_are_refused_before_parsing() -> None:
    """A hostile client must not be able to make us allocate."""
    oversized = json.dumps({"type": "hello", "protocol_version": 1, "device_id": "x" * 20000})
    assert decode_client_message(oversized).reason == "frame_too_large"
    assert decode_client_message(b"\x00" * (MAX_TEXT_FRAME_BYTES + 1)).reason == "frame_too_large"


def test_invalid_utf8_is_handled() -> None:
    assert decode_client_message(b"\xff\xfe\x00garbage").reason == "not_utf8"


def test_unknown_fields_are_ignored_so_newer_firmware_still_works() -> None:
    """Forward compatibility: extra keys from a future firmware are not an error."""
    message = decode_client_message(
        json.dumps({"type": "utterance_end", "sample_count": 10, "future_field": "x"})
    )
    assert isinstance(message, UtteranceEndMessage)
    assert message.sample_count == 10


def test_server_messages_encode_to_the_documented_shape() -> None:
    assert json.loads(encode(StateMessage(state=SystemState.THINKING))) == {
        "type": "state",
        "state": "thinking",
    }
    expression = json.loads(
        encode(ExpressionMessage(expression=Expression.HAPPY, hold_ms=1800))
    )
    assert expression == {"type": "expression", "expression": "happy", "hold_ms": 1800}


def test_speak_start_carries_the_audio_format() -> None:
    payload = json.loads(encode(SpeakStartMessage(turn_id=7)))
    assert payload["sample_rate"] == AUDIO_SAMPLE_RATE
    assert payload["bits_per_sample"] == AUDIO_BITS_PER_SAMPLE
    assert payload["channels"] == AUDIO_CHANNELS


def test_expression_hold_is_bounded() -> None:
    """A hold long enough to freeze the face must be rejected at the boundary."""
    with pytest.raises(Exception):
        ExpressionMessage(expression=Expression.HAPPY, hold_ms=10**9)
    with pytest.raises(Exception):
        ExpressionMessage(expression=Expression.HAPPY, hold_ms=-1)


def test_utterance_cap_is_a_sane_duration() -> None:
    seconds = MAX_UTTERANCE_BYTES / (AUDIO_SAMPLE_RATE * 2)
    assert 10 <= seconds <= 60


def test_protocol_version_is_declared() -> None:
    assert isinstance(PROTOCOL_VERSION, int) and PROTOCOL_VERSION >= 1
