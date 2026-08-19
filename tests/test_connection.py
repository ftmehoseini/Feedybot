"""End-to-end WebSocket tests against the real app.

These run the actual FastAPI application over a real (in-process) WebSocket, using the
same frames the firmware sends. They are the closest thing to hardware that runs in CI.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.audio import tone
from backend.main import app
from backend.protocol import PROTOCOL_VERSION

UTTERANCE = tone(1500, frequency_hz=200.0)
SILENCE_SHORT = b"\x00\x00" * 800  # 100 ms: below the minimum utterance length


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def handshake(
    ws, *, version: int = PROTOCOL_VERSION, device_id: str = "test-robot", drain: bool = True
) -> dict:
    """Complete the handshake and, by default, drain the frames that follow it.

    A successful handshake is followed by `state: idle` and `listen_control: true`.
    Draining them here keeps every later assertion about the *turn* rather than about
    connection setup.
    """
    ws.send_text(
        json.dumps(
            {
                "type": "hello",
                "protocol_version": version,
                "device_id": device_id,
                "firmware_version": "test",
            }
        )
    )
    ack = json.loads(ws.receive_text())
    if drain and ack.get("accepted"):
        json.loads(ws.receive_text())  # state: idle
        json.loads(ws.receive_text())  # listen_control: true
    return ack


def send_utterance(ws, pcm: bytes) -> None:
    ws.send_text(json.dumps({"type": "utterance_start"}))
    for offset in range(0, len(pcm), 2048):
        ws.send_bytes(pcm[offset : offset + 2048])
    ws.send_text(json.dumps({"type": "utterance_end", "sample_count": len(pcm) // 2}))


def collect_turn(ws, *, limit: int = 60) -> tuple[list[dict], int]:
    """Read until the turn is over. Returns (control messages, audio bytes received)."""
    messages: list[dict] = []
    audio_bytes = 0
    saw_speak_end = False
    for _ in range(limit):
        frame = ws.receive()
        if frame.get("bytes") is not None:
            audio_bytes += len(frame["bytes"])
            continue
        if frame.get("text") is None:
            break
        message = json.loads(frame["text"])
        messages.append(message)
        if message["type"] == "speak_end":
            saw_speak_end = True
            ws.send_text(json.dumps({"type": "playback_done", "turn_id": message["turn_id"]}))
        if saw_speak_end and message["type"] == "state" and message["state"] == "listening":
            break
    return messages, audio_bytes


def test_handshake_succeeds_and_reports_the_role(client) -> None:
    with client.websocket_connect("/ws/robot") as ws:
        ack = handshake(ws)
        assert ack["type"] == "hello_ack"
        assert ack["accepted"] is True
        assert ack["protocol_version"] == PROTOCOL_VERSION
        assert ack["role_id"] == "social_companion"
        # The device learns its resting face from the role rather than hard-coding one.
        assert ack["resting_expression"] == "neutral"
        assert ack["session_id"]


def test_protocol_version_mismatch_is_refused_loudly(client) -> None:
    """Refusing beats misparsing: a silent version skew costs hours on a bench."""
    with client.websocket_connect("/ws/robot") as ws:
        ack = handshake(ws, version=PROTOCOL_VERSION + 99, drain=False)
        assert ack["accepted"] is False
        assert "version" in ack["reason"].lower()


def test_a_non_hello_first_frame_is_refused(client) -> None:
    with client.websocket_connect("/ws/robot") as ws:
        ws.send_text(json.dumps({"type": "utterance_start"}))
        ack = json.loads(ws.receive_text())
        assert ack["accepted"] is False


def test_full_turn_produces_speech_and_returns_to_listening(client) -> None:
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        send_utterance(ws, UTTERANCE)
        messages, audio_bytes = collect_turn(ws)

        types = [m["type"] for m in messages]
        assert "speak_start" in types and "speak_end" in types
        assert audio_bytes > 0

        states = [m["state"] for m in messages if m["type"] == "state"]
        assert "processing" in states and "thinking" in states and "speaking" in states
        assert states[-1] == "listening"


def test_playback_done_returns_the_robot_to_listening(client) -> None:
    """The pipeline stops at SPEAKING; this confirmation is what closes the loop."""
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        send_utterance(ws, UTTERANCE)
        messages, _ = collect_turn(ws)
        final_state = [m for m in messages if m["type"] == "state"][-1]
        assert final_state["state"] == "listening"


def test_microphone_is_gated_closed_while_speaking(client) -> None:
    """Half-duplex: the robot must not be able to transcribe its own voice."""
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        send_utterance(ws, UTTERANCE)
        messages, _ = collect_turn(ws)

        timeline = [
            (m["type"], m.get("listening", m.get("state")))
            for m in messages
            if m["type"] in {"listen_control", "state"}
        ]
        closed_at = next(
            i for i, (kind, value) in enumerate(timeline)
            if kind == "listen_control" and value is False
        )
        speaking_at = next(
            i for i, (kind, value) in enumerate(timeline)
            if kind == "state" and value == "speaking"
        )
        assert closed_at < speaking_at, "the gate must close before the audio starts"


def test_multi_turn_conversation(client) -> None:
    """The V1 acceptance criterion: several turns in a row, without wedging."""
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        for turn in range(1, 4):
            send_utterance(ws, UTTERANCE)
            messages, audio_bytes = collect_turn(ws)
            assert audio_bytes > 0, f"turn {turn} produced no speech"
            speak_starts = [m for m in messages if m["type"] == "speak_start"]
            assert speak_starts[0]["turn_id"] == turn


def test_malformed_frames_do_not_break_the_connection(client) -> None:
    """A wedged device sending garbage must not take the conversation down with it."""
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        for garbage in [
            "not json",
            "[]",
            "{}",
            '{"type": "invented_by_a_future_firmware"}',
            '{"type": "interaction", "event": "headbutt"}',
        ]:
            ws.send_text(garbage)

        # The connection still works afterwards.
        send_utterance(ws, UTTERANCE)
        _, audio_bytes = collect_turn(ws)
        assert audio_bytes > 0


def test_audio_outside_an_utterance_is_dropped(client) -> None:
    """Binary frames with no utterance window must not accumulate or start a turn."""
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        for offset in range(0, len(UTTERANCE), 2048):
            ws.send_bytes(UTTERANCE[offset : offset + 2048])

        # Now do a real turn: the stray audio must not have polluted it.
        send_utterance(ws, UTTERANCE)
        messages, audio_bytes = collect_turn(ws)
        assert audio_bytes > 0
        speak_starts = [m for m in messages if m["type"] == "speak_start"]
        assert speak_starts[0]["turn_id"] == 1, "stray audio created a phantom turn"


def test_utterances_below_the_minimum_are_ignored(client) -> None:
    """A click or a door bump should cost nothing — no STT call, no reply.

    Asserted by turn numbering rather than by frame timing: if the short utterance had
    started a turn, the following real one would be turn 2.
    """
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        send_utterance(ws, SILENCE_SHORT)
        send_utterance(ws, UTTERANCE)
        messages, audio_bytes = collect_turn(ws)

        assert audio_bytes > 0
        speak_starts = [m for m in messages if m["type"] == "speak_start"]
        assert len(speak_starts) == 1, "the short utterance started a turn of its own"
        assert speak_starts[0]["turn_id"] == 1


def test_long_touch_cancels_and_reopens_the_microphone(client) -> None:
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        ws.send_text(json.dumps({"type": "interaction", "event": "long_touch"}))
        seen = [json.loads(ws.receive_text()) for _ in range(2)]
        assert any(m["type"] == "state" and m["state"] == "listening" for m in seen)
        # Cancelling must leave the robot able to hear the next thing said to it.
        assert any(m["type"] == "listen_control" and m["listening"] for m in seen)


def test_short_touch_opens_the_microphone(client) -> None:
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        ws.send_text(json.dumps({"type": "interaction", "event": "short_touch"}))
        seen = [json.loads(ws.receive_text()) for _ in range(3)]
        assert any(m["type"] == "listen_control" and m["listening"] for m in seen)
        assert any(m["type"] == "expression" for m in seen), "a touch should be acknowledged"


def test_reconnect_starts_a_fresh_conversation(client) -> None:
    """After a gap the person has moved on; resuming mid-thought is worse than a reset."""
    with client.websocket_connect("/ws/robot") as ws:
        first = handshake(ws)
        send_utterance(ws, UTTERANCE)
        collect_turn(ws)

    with client.websocket_connect("/ws/robot") as ws:
        second = handshake(ws)
        assert second["session_id"] != first["session_id"]
        send_utterance(ws, UTTERANCE)
        messages, _ = collect_turn(ws)
        speak_starts = [m for m in messages if m["type"] == "speak_start"]
        # Turn numbering restarts: the old turn is not resumed.
        assert speak_starts[0]["turn_id"] == 1


def test_device_status_is_accepted_without_disturbing_anything(client) -> None:
    with client.websocket_connect("/ws/robot") as ws:
        handshake(ws)
        ws.send_text(
            json.dumps({"type": "device_status", "free_heap": 180000, "rssi": -55})
        )
        send_utterance(ws, UTTERANCE)
        _, audio_bytes = collect_turn(ws)
        assert audio_bytes > 0


def test_health_and_roles_endpoints(client) -> None:
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["protocol_version"] == PROTOCOL_VERSION

    roles = client.get("/roles").json()
    assert roles["active"] == "social_companion"
    assert "english_teacher" in roles["available"]
