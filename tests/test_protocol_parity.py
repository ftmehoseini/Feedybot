"""The firmware and the backend must agree on the protocol.

`backend/protocol.py` and `firmware/include/protocol_constants.h` define the same
contract in two languages. They cannot share a definition, so this test reads both files
and fails when they drift — which is the failure mode that otherwise shows up as a robot
that connects, does nothing, and gives no clue why.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend import protocol

FIRMWARE_HEADER = Path(__file__).resolve().parent.parent / "firmware/include/protocol_constants.h"
FIRMWARE_CONFIG = Path(__file__).resolve().parent.parent / "firmware/include/config.h"


def firmware_defines(path: Path) -> dict[str, str]:
    """Every `#define NAME value` in a header, as strings."""
    text = path.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for match in re.finditer(r'^#define\s+(\w+)\s+(.+?)\s*(?://.*)?$', text, re.MULTILINE):
        found[match.group(1)] = match.group(2).strip()
    return found


@pytest.fixture(scope="module")
def defines() -> dict[str, str]:
    return firmware_defines(FIRMWARE_HEADER)


@pytest.fixture(scope="module")
def config_defines() -> dict[str, str]:
    return firmware_defines(FIRMWARE_CONFIG)


def test_protocol_version_matches(defines: dict[str, str]) -> None:
    assert int(defines["FAFOBOT_PROTOCOL_VERSION"]) == protocol.PROTOCOL_VERSION


def test_frame_limits_match(defines: dict[str, str]) -> None:
    assert int(defines["MAX_TEXT_FRAME_BYTES"]) == protocol.MAX_TEXT_FRAME_BYTES
    assert int(defines["MAX_BINARY_FRAME_BYTES"]) == protocol.MAX_BINARY_FRAME_BYTES


def test_audio_format_matches(config_defines: dict[str, str]) -> None:
    assert int(config_defines["AUDIO_SAMPLE_RATE"]) == protocol.AUDIO_SAMPLE_RATE
    assert int(config_defines["AUDIO_BITS_PER_SAMPLE"]) == protocol.AUDIO_BITS_PER_SAMPLE
    assert int(config_defines["AUDIO_CHANNELS"]) == protocol.AUDIO_CHANNELS


def test_firmware_audio_chunk_fits_the_backend_frame_limit(
    config_defines: dict[str, str],
) -> None:
    """A chunk larger than the backend's limit would be silently dropped as audio."""
    chunk = int(config_defines["WS_AUDIO_CHUNK_BYTES"])
    assert chunk <= protocol.MAX_BINARY_FRAME_BYTES


def test_firmware_utterance_cap_is_within_the_backend_cap(
    config_defines: dict[str, str],
) -> None:
    device_ms = int(config_defines["AUDIO_MAX_UTTERANCE_MS"])
    backend_ms = protocol.MAX_UTTERANCE_BYTES / (protocol.AUDIO_SAMPLE_RATE * 2) * 1000
    assert device_ms <= backend_ms, "the device would upload more than the backend accepts"


def test_every_message_type_string_exists_on_both_sides(defines: dict[str, str]) -> None:
    """The firmware's message strings must all be types the backend knows."""
    firmware_types = {
        value.strip('"')
        for name, value in defines.items()
        if name.startswith("MSG_")
    }
    backend_client_types = {
        "hello", "utterance_start", "utterance_end", "cancel", "playback_done",
        "interaction", "device_status",
    }
    backend_server_types = {
        "hello_ack", "state", "expression", "listen_control", "speak_start",
        "speak_end", "error",
    }
    assert firmware_types == backend_client_types | backend_server_types


def test_interaction_event_strings_match(defines: dict[str, str]) -> None:
    from backend.protocol import InteractionMessage

    firmware_events = {defines["EVENT_SHORT_TOUCH"].strip('"'),
                       defines["EVENT_LONG_TOUCH"].strip('"')}
    schema = InteractionMessage.model_json_schema()
    backend_events = set(schema["properties"]["event"]["enum"])
    assert firmware_events == backend_events


def test_expression_and_state_names_match_the_firmware_parsers() -> None:
    """The firmware parses these by string. A rename on one side is a silent bug."""
    from backend.emotion import Expression, SystemState

    source = (Path(__file__).resolve().parent.parent / "firmware/include/robot_state.h").read_text()
    for expression in Expression:
        assert f'"{expression.value}"' in source, f"firmware cannot parse {expression.value}"
    for state in SystemState:
        assert f'"{state.value}"' in source, f"firmware cannot parse {state.value}"
