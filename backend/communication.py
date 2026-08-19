"""WebSocket transport: framing, validation, and the connection state machine.

This module is the trust boundary. Everything arriving here is from a device that may be
buggy, mid-firmware-upgrade, or hostile, so it validates before it allocates:

- frame sizes are checked before parsing,
- binary audio outside an utterance is dropped rather than buffered,
- the utterance buffer has a hard cap independent of anything the device claims,
- unknown message types are counted and ignored, never fatal.

It also owns the half-duplex gate: the microphone is closed while the robot speaks and
reopened only after `playback_done` (or its timeout), which is what stops the robot
transcribing its own voice.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.audio import pcm_duration_ms
from backend.config import Settings
from backend.emotion import Expression, SystemState
from backend.logging_setup import log_event
from backend.pipeline import ConversationPipeline
from backend.protocol import (
    AUDIO_SAMPLE_RATE,
    MAX_BINARY_FRAME_BYTES,
    MAX_UTTERANCE_BYTES,
    PROTOCOL_VERSION,
    CancelMessage,
    DeviceStatusMessage,
    ErrorMessage,
    ExpressionMessage,
    HelloAckMessage,
    HelloMessage,
    InteractionMessage,
    ListenControlMessage,
    PlaybackDoneMessage,
    SpeakEndMessage,
    SpeakStartMessage,
    StateMessage,
    UnknownMessage,
    UtteranceEndMessage,
    UtteranceStartMessage,
    decode_client_message,
    encode,
)
from backend.roles.schema import RolePack
from backend.session import RobotSession

logger = logging.getLogger(__name__)

#: Below this, an "utterance" is a click or a door slam. Cheaper to drop here than to
#: pay for an STT call that returns nothing.
MIN_UTTERANCE_BYTES = int(0.2 * AUDIO_SAMPLE_RATE * 2)


class RobotConnection:
    """One connected robot: transport, session, and the turn loop.

    Implements `pipeline.TurnSink`, so the pipeline writes its effects straight onto the
    socket without knowing what a socket is.
    """

    def __init__(
        self,
        websocket: WebSocket,
        *,
        role: RolePack,
        pipeline: ConversationPipeline,
        settings: Settings,
    ) -> None:
        self._ws = websocket
        self._settings = settings
        self._pipeline = pipeline
        self.session = RobotSession(role=role)

        self._utterance = bytearray()
        self._receiving_utterance = False
        self._utterance_overflowed = False
        #: Closed while the robot speaks. Half-duplex: see docs/ARCHITECTURE.md.
        self._microphone_open = False
        self._turn_task: asyncio.Task[Any] | None = None
        self._playback_done = asyncio.Event()

        # Counters, surfaced in the disconnect log. Cheap, and they answer "is the
        # device misbehaving?" without a packet capture.
        self.dropped_frames = 0
        self.dropped_audio_bytes = 0

    # -- lifecycle -------------------------------------------------------------------

    async def run(self) -> None:
        """Serve this connection until the robot disconnects."""
        try:
            if not await self._handshake():
                return
            await self._receive_loop()
        except WebSocketDisconnect:
            pass
        finally:
            await self._teardown()

    async def _handshake(self) -> bool:
        """Exchange hello/hello_ack. Returns False if the connection must not proceed."""
        try:
            async with asyncio.timeout(10.0):
                raw = await self._ws.receive_text()
        except (asyncio.TimeoutError, WebSocketDisconnect):
            log_event(logger, logging.WARNING, "handshake timed out or aborted")
            return False

        message = decode_client_message(raw)
        if not isinstance(message, HelloMessage):
            await self._send(
                HelloAckMessage(accepted=False, reason="expected a hello frame first")
            )
            return False

        if message.protocol_version != PROTOCOL_VERSION:
            # Refuse loudly. A silent misparse between versions produces bizarre
            # behaviour that costs hours to diagnose on a bench.
            await self._send(
                HelloAckMessage(
                    accepted=False,
                    reason=(
                        f"protocol version mismatch: robot speaks v{message.protocol_version}, "
                        f"backend speaks v{PROTOCOL_VERSION}"
                    ),
                )
            )
            log_event(
                logger,
                logging.WARNING,
                "rejected connection on protocol version",
                device_id=message.device_id,
                device_version=message.protocol_version,
                server_version=PROTOCOL_VERSION,
            )
            return False

        # V1 accepts any auth_token without verification. The field exists so that
        # adding verification later is not a protocol break. Do not mistake this for
        # authentication -- see docs/ARCHITECTURE_AUDIT.md section 3.5.
        self.session.device_id = message.device_id

        await self._send(
            HelloAckMessage(
                accepted=True,
                session_id=self.session.session_id,
                role_id=self.session.role.id,
                resting_expression=self.session.role.emotion_policy.resting,
            )
        )
        log_event(
            logger,
            logging.INFO,
            "robot connected",
            session_id=self.session.session_id,
            device_id=message.device_id,
            firmware_version=message.firmware_version,
            role=self.session.role.id,
        )

        # A reconnect must not resume a turn from before the gap: the human has moved on.
        self.session.reset_conversation()
        self.session.force_state(SystemState.IDLE)
        await self.set_state(SystemState.IDLE)
        await self._open_microphone()
        return True

    async def _teardown(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self.session.cancel_turn()
            self._turn_task.cancel()
            try:
                await self._turn_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.session.force_state(SystemState.OFFLINE)
        log_event(
            logger,
            logging.INFO,
            "robot disconnected",
            session_id=self.session.session_id,
            device_id=self.session.device_id,
            turns=self.session.turn_count,
            dropped_frames=self.dropped_frames,
            dropped_audio_bytes=self.dropped_audio_bytes,
        )

    # -- receive loop -----------------------------------------------------------------

    async def _receive_loop(self) -> None:
        while True:
            frame = await self._ws.receive()
            if frame.get("type") == "websocket.disconnect":
                return
            if (text := frame.get("text")) is not None:
                await self._handle_text(text)
            elif (payload := frame.get("bytes")) is not None:
                self._handle_binary(payload)

    async def _handle_text(self, raw: str) -> None:
        message = decode_client_message(raw)
        self.session.touch()

        if isinstance(message, UnknownMessage):
            self.dropped_frames += 1
            log_event(
                logger,
                logging.DEBUG,
                "dropped unrecognised frame",
                session_id=self.session.session_id,
                reason=message.reason,
                raw_type=message.raw_type,
            )
            return

        if isinstance(message, UtteranceStartMessage):
            self._begin_utterance()
        elif isinstance(message, UtteranceEndMessage):
            await self._end_utterance()
        elif isinstance(message, CancelMessage):
            await self._cancel()
        elif isinstance(message, PlaybackDoneMessage):
            self._playback_done.set()
        elif isinstance(message, InteractionMessage):
            await self._handle_interaction(message)
        elif isinstance(message, DeviceStatusMessage):
            log_event(
                logger,
                logging.DEBUG,
                "device status",
                session_id=self.session.session_id,
                free_heap=message.free_heap,
                rssi=message.rssi,
                dropped_audio_chunks=message.dropped_audio_chunks,
            )
        elif isinstance(message, HelloMessage):
            # A second hello means the device restarted its state machine without
            # dropping TCP. Treat it as a fresh session rather than ignoring it.
            self.session.reset_conversation()
            await self.set_state(SystemState.IDLE)

    def _handle_binary(self, payload: bytes) -> None:
        """Accumulate PCM. Synchronous on purpose: this is the hot path."""
        if len(payload) > MAX_BINARY_FRAME_BYTES:
            self.dropped_frames += 1
            return
        if not self._receiving_utterance or not self._microphone_open:
            # Audio outside an utterance window, or while the robot is speaking. Never
            # buffered: this is exactly the path a wedged device would use to exhaust
            # our memory, and it is also the robot hearing itself.
            self.dropped_audio_bytes += len(payload)
            return
        if len(self._utterance) + len(payload) > MAX_UTTERANCE_BYTES:
            self._utterance_overflowed = True
            self.dropped_audio_bytes += len(payload)
            return
        self._utterance.extend(payload)

    # -- turn handling -------------------------------------------------------------------

    def _begin_utterance(self) -> None:
        if self._turn_task and not self._turn_task.done():
            # The device started talking while we were still processing the last turn.
            # Ignore the new audio rather than racing: half-duplex means one turn at a
            # time, and the device's own gate should have prevented this.
            log_event(
                logger,
                logging.DEBUG,
                "utterance_start while a turn is active; ignoring",
                session_id=self.session.session_id,
            )
            return
        self._utterance.clear()
        self._utterance_overflowed = False
        self._receiving_utterance = True
        self.session.transition(SystemState.LISTENING)

    async def _end_utterance(self) -> None:
        if not self._receiving_utterance:
            return
        self._receiving_utterance = False
        pcm = bytes(self._utterance)
        self._utterance.clear()

        if self._utterance_overflowed:
            log_event(
                logger,
                logging.WARNING,
                "utterance exceeded the maximum length and was truncated",
                session_id=self.session.session_id,
                kept_bytes=len(pcm),
            )
        if len(pcm) < MIN_UTTERANCE_BYTES:
            log_event(
                logger,
                logging.DEBUG,
                "ignoring utterance below the minimum length",
                session_id=self.session.session_id,
                bytes=len(pcm),
            )
            self.session.transition(SystemState.LISTENING)
            return

        self._turn_task = asyncio.create_task(self._run_turn(pcm))

    async def _run_turn(self, pcm: bytes) -> None:
        """Own one turn end to end, including waiting for playback to finish."""
        async with self.session.turn_lock:
            await self._close_microphone()
            try:
                self._playback_done.clear()
                result = await self._pipeline.run_turn(self.session, pcm, self)
                if result.audio_bytes:
                    await self._await_playback(result.audio_bytes)
            except Exception as exc:  # noqa: BLE001 - a turn must never kill the socket
                log_event(
                    logger,
                    logging.ERROR,
                    "unhandled error during turn",
                    session_id=self.session.session_id,
                    error_detail=str(exc),
                )
                self.session.force_state(SystemState.LISTENING)
                await self.set_state(SystemState.LISTENING)
            finally:
                await self._open_microphone()

    async def _await_playback(self, audio_bytes: int) -> None:
        """Wait for `playback_done`, bounded by the audio's own duration plus grace.

        Without the bound, a robot that reboots mid-sentence would leave the backend in
        SPEAKING forever and the conversation would simply stop.
        """
        expected_s = pcm_duration_ms(b"\x00" * audio_bytes, AUDIO_SAMPLE_RATE) / 1000
        budget = expected_s + self._settings.playback_grace_s
        started = time.monotonic()
        try:
            async with asyncio.timeout(budget):
                await self._playback_done.wait()
        except asyncio.TimeoutError:
            log_event(
                logger,
                logging.WARNING,
                "playback_done never arrived; forcing state",
                session_id=self.session.session_id,
                turn_id=self.session.turn_id,
                waited_s=round(time.monotonic() - started, 2),
            )
        if self.session.metrics:
            self.session.metrics.playback_done_at = time.monotonic()
            # Logged here rather than in the pipeline: `total_ms` only exists once the
            # robot confirms it finished speaking, which is strictly after the pipeline
            # has returned. Logging it there would emit a permanent null.
            log_event(
                logger,
                logging.INFO,
                "turn playback complete",
                session_id=self.session.session_id,
                turn_id=self.session.turn_id,
                **self.session.metrics.summary(),
            )
        self.session.force_state(SystemState.LISTENING)
        await self.set_state(SystemState.LISTENING)

    async def _cancel(self) -> None:
        self.session.cancel_turn()
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self._playback_done.set()
        self.session.force_state(SystemState.LISTENING)
        await self.set_state(SystemState.LISTENING)
        await self._open_microphone()

    async def _handle_interaction(self, message: InteractionMessage) -> None:
        """Physical interaction, already classified on-device.

        Kept deliberately thin: gesture semantics belong on the MCU where the timing is,
        and the backend only reacts to the meaning.
        """
        if message.event == "long_touch":
            await self._cancel()
            return
        # Short touch: acknowledge and make sure we are listening. Useful when the human
        # wants to start a turn without waiting for VAD.
        if self.session.state in (SystemState.IDLE, SystemState.LISTENING, SystemState.ERROR):
            await self.set_expression(self.session.role.emotion_policy.resting, 800)
            self.session.force_state(SystemState.LISTENING)
            await self.set_state(SystemState.LISTENING)
            await self._open_microphone()

    # -- microphone gate ------------------------------------------------------------------

    async def _open_microphone(self) -> None:
        self._microphone_open = True
        await self._send(ListenControlMessage(listening=True))

    async def _close_microphone(self) -> None:
        self._microphone_open = False
        await self._send(ListenControlMessage(listening=False))

    # -- TurnSink implementation ------------------------------------------------------------

    async def set_state(self, state: SystemState) -> None:
        await self._send(StateMessage(state=state))

    async def set_expression(self, expression: Expression, hold_ms: int) -> None:
        await self._send(ExpressionMessage(expression=expression, hold_ms=hold_ms))

    async def speak_start(self, turn_id: int, sample_rate: int) -> None:
        await self._send(SpeakStartMessage(turn_id=turn_id, sample_rate=sample_rate))

    async def speak_audio(self, pcm: bytes) -> None:
        if self._ws.client_state is WebSocketState.CONNECTED:
            await self._ws.send_bytes(pcm)

    async def speak_end(self, turn_id: int) -> None:
        await self._send(SpeakEndMessage(turn_id=turn_id))

    async def report_error(self, code: str, message: str) -> None:
        await self._send(ErrorMessage(code=code, message=message[:200]))

    # -- transport -----------------------------------------------------------------------

    async def _send(self, message: Any) -> None:
        """Send a control frame, tolerating a socket that closed underneath us."""
        if self._ws.client_state is not WebSocketState.CONNECTED:
            return
        try:
            await self._ws.send_text(encode(message))
        except (RuntimeError, WebSocketDisconnect):
            # The robot vanished mid-turn. The receive loop will notice; nothing here
            # needs to escalate.
            pass
