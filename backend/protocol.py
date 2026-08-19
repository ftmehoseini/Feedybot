"""The wire contract between robot and backend. Single source of truth.

Control messages are JSON text frames; audio is raw binary frames. Both directions use
the same envelope: a `type` discriminator plus type-specific fields.

Two rules govern every change to this module:

1. **Unknown message types must never crash either side.** `decode_client_message`
   returns an `UnknownMessage` instead of raising, so a newer robot talking to an older
   backend degrades rather than disconnects.
2. **The constants here are mirrored in `firmware/include/protocol_constants.h`.** They
   are duplicated across a language boundary, not across a module boundary; the test
   suite asserts the two stay in step.

See `docs/PROTOCOL.md` for the narrative contract and message ordering.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.emotion import Expression, SystemState

#: Bumped on any breaking change to message shape or ordering. The handshake refuses a
#: mismatch loudly rather than misparsing quietly.
PROTOCOL_VERSION = 1

#: Audio format for V1. Chosen for MCU cost, not fidelity: 16 kHz/16-bit/mono is the
#: cheapest format every STT provider accepts natively, and it halves the I2S and
#: network budget versus 32 kHz. Both directions use it.
AUDIO_SAMPLE_RATE = 16_000
AUDIO_BITS_PER_SAMPLE = 16
AUDIO_CHANNELS = 1
AUDIO_BYTES_PER_SAMPLE = AUDIO_BITS_PER_SAMPLE // 8

#: Frame size limits, enforced *before* parsing. The device is not trusted; a wedged or
#: hostile client must not be able to allocate unbounded memory on the backend.
MAX_TEXT_FRAME_BYTES = 8 * 1024
MAX_BINARY_FRAME_BYTES = 8 * 1024

#: Hard ceiling on one utterance, independent of the firmware's own cap. 30 s of
#: 16 kHz/16-bit mono audio.
MAX_UTTERANCE_BYTES = 30 * AUDIO_SAMPLE_RATE * AUDIO_BYTES_PER_SAMPLE


class _Message(BaseModel):
    """Base envelope. Extra fields are ignored so a newer peer can add fields safely."""

    model_config = ConfigDict(extra="ignore", frozen=True)


# --------------------------------------------------------------------------------------
# Robot -> backend
# --------------------------------------------------------------------------------------


class DeviceCapabilities(_Message):
    """What the robot claims it can do. Advisory; the backend still enforces limits."""

    sample_rate: int = AUDIO_SAMPLE_RATE
    bits_per_sample: int = AUDIO_BITS_PER_SAMPLE
    channels: int = AUDIO_CHANNELS
    has_display: bool = True
    has_speaker: bool = True
    has_microphone: bool = True


class HelloMessage(_Message):
    """First frame on every connection. Carries identity and version."""

    type: Literal["hello"] = "hello"
    protocol_version: int
    device_id: str = "unknown"
    firmware_version: str = "unknown"
    #: Placeholder for device authentication. V1 accepts any value without verifying it;
    #: the field exists so adding verification is not a protocol break.
    auth_token: str | None = None
    capabilities: DeviceCapabilities = Field(default_factory=DeviceCapabilities)


class UtteranceStartMessage(_Message):
    """The robot's VAD detected speech onset. Binary PCM frames follow."""

    type: Literal["utterance_start"] = "utterance_start"


class UtteranceEndMessage(_Message):
    """Speech ended. `sample_count` lets the backend detect truncation."""

    type: Literal["utterance_end"] = "utterance_end"
    sample_count: int | None = None


class CancelMessage(_Message):
    """Abandon the current turn (long press, or the user walked away)."""

    type: Literal["cancel"] = "cancel"


class PlaybackDoneMessage(_Message):
    """Playback of `turn_id` finished. Releases the backend from SPEAKING."""

    type: Literal["playback_done"] = "playback_done"
    turn_id: int | None = None


class InteractionMessage(_Message):
    """A physical interaction event, already debounced and classified on-device."""

    type: Literal["interaction"] = "interaction"
    event: Literal["short_touch", "long_touch"]


class DeviceStatusMessage(_Message):
    """Optional telemetry. Never required for correctness."""

    type: Literal["device_status"] = "device_status"
    free_heap: int | None = None
    rssi: int | None = None
    uptime_ms: int | None = None
    dropped_audio_chunks: int | None = None


class UnknownMessage(_Message):
    """A frame we could not interpret. Carried, counted, and ignored — never raised."""

    type: Literal["__unknown__"] = "__unknown__"
    raw_type: str | None = None
    reason: str = "unknown_type"


ClientMessage = Annotated[
    Union[
        HelloMessage,
        UtteranceStartMessage,
        UtteranceEndMessage,
        CancelMessage,
        PlaybackDoneMessage,
        InteractionMessage,
        DeviceStatusMessage,
    ],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------------------
# Backend -> robot
# --------------------------------------------------------------------------------------


class HelloAckMessage(_Message):
    """Handshake response. `accepted=False` means the robot must not proceed."""

    type: Literal["hello_ack"] = "hello_ack"
    protocol_version: int = PROTOCOL_VERSION
    accepted: bool = True
    session_id: str = ""
    role_id: str = ""
    #: The face the robot returns to when nothing else is happening. Role-dependent, so
    #: the device learns it at runtime rather than hard-coding it.
    resting_expression: Expression = Expression.NEUTRAL
    reason: str | None = None


class StateMessage(_Message):
    """Authoritative system state. The device may lead locally but must converge here."""

    type: Literal["state"] = "state"
    state: SystemState


class ExpressionMessage(_Message):
    """A social expression to show for `hold_ms`, after which the device decays locally."""

    type: Literal["expression"] = "expression"
    expression: Expression
    hold_ms: int = Field(default=1800, ge=0, le=60_000)


class ListenControlMessage(_Message):
    """Explicitly open or close the microphone gate.

    Half-duplex in V1: the backend closes the gate while speaking so the robot cannot
    hear itself and start a phantom turn.
    """

    type: Literal["listen_control"] = "listen_control"
    listening: bool


class SpeakStartMessage(_Message):
    """Binary PCM frames follow until `speak_end`."""

    type: Literal["speak_start"] = "speak_start"
    turn_id: int
    sample_rate: int = AUDIO_SAMPLE_RATE
    bits_per_sample: int = AUDIO_BITS_PER_SAMPLE
    channels: int = AUDIO_CHANNELS
    #: Advisory only. The mouth is driven by measured amplitude, never by this number.
    estimated_ms: int | None = None


class SpeakEndMessage(_Message):
    """All PCM for `turn_id` has been sent. The device replies with `playback_done`."""

    type: Literal["speak_end"] = "speak_end"
    turn_id: int


class ErrorMessage(_Message):
    """Technical detail for the device log. Never spoken to the human."""

    type: Literal["error"] = "error"
    code: str
    message: str = ""
    recoverable: bool = True


ServerMessage = Union[
    HelloAckMessage,
    StateMessage,
    ExpressionMessage,
    ListenControlMessage,
    SpeakStartMessage,
    SpeakEndMessage,
    ErrorMessage,
]


class _ClientEnvelope(BaseModel):
    """Wrapper used only to drive pydantic's discriminated-union parsing."""

    model_config = ConfigDict(extra="ignore")
    message: ClientMessage


def decode_client_message(raw: str | bytes) -> ClientMessage | UnknownMessage:
    """Parse one text frame from the robot.

    Never raises on bad input: malformed JSON, a missing discriminator, or a type we do
    not know all produce an `UnknownMessage`. Callers count those and carry on, which is
    what keeps a version-skewed robot connected instead of crash-looping.
    """
    if isinstance(raw, bytes):
        if len(raw) > MAX_TEXT_FRAME_BYTES:
            return UnknownMessage(reason="frame_too_large")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return UnknownMessage(reason="not_utf8")
    elif len(raw.encode("utf-8", errors="ignore")) > MAX_TEXT_FRAME_BYTES:
        return UnknownMessage(reason="frame_too_large")

    try:
        payload: Any = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return UnknownMessage(reason="invalid_json")

    if not isinstance(payload, dict):
        return UnknownMessage(reason="not_an_object")

    raw_type = payload.get("type")
    if not isinstance(raw_type, str):
        return UnknownMessage(reason="missing_type")

    try:
        return _ClientEnvelope(message=payload).message
    except ValidationError as exc:
        # A known type with bad fields is still a protocol violation, but a survivable
        # one: report why, keep the connection. `union_tag_invalid` is pydantic's way of
        # saying "no branch of the union claims this discriminator" — i.e. a message
        # type from a future firmware, which is expected and harmless.
        tags = {err["type"] for err in exc.errors()}
        reason = "unknown_type" if "union_tag_invalid" in tags else "invalid_fields"
        return UnknownMessage(raw_type=raw_type, reason=reason)


def encode(message: BaseModel) -> str:
    """Serialise a server message to a JSON text frame."""
    return message.model_dump_json(exclude_none=True)
