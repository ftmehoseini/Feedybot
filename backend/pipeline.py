"""One conversational turn: audio in, audio out.

This module is where the product's reliability promises are actually kept, so it is
written defensively:

- **Every stage has a timeout.** There is no code path that can leave the robot in
  THINKING forever; a stage that blows its budget produces a spoken apology and returns
  the machine to LISTENING.
- **Every stage checks for cancellation** before doing expensive work, so a long press
  stops the turn rather than merely hiding its result.
- **Every failure has a social consequence**, chosen from `fallbacks`, and a technical
  log line. The human hears the former, never the latter.
- **No vendor is named anywhere in this file.** It talks to three `Protocol` types.

The pipeline emits its effects through a `TurnSink` rather than a WebSocket, which is
what lets the whole thing be tested without a network and reused by the simulator.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from backend.agent_reply import AgentReply, parse_agent_output
from backend.audio import pcm_duration_ms
from backend.config import Settings
from backend.emotion import (
    DEFAULT_EXPRESSION_HOLD_MS,
    Expression,
    SystemState,
)
from backend.errors import ErrorCategory, ProviderError, TurnCancelled
from backend.fallbacks import fallback_expression, fallback_speech
from backend.logging_setup import log_event
from backend.protocol import AUDIO_SAMPLE_RATE
from backend.providers.base import Transcript
from backend.providers.registry import ProviderBundle
from backend.roles.prompt_builder import (
    DeploymentContext,
    SessionContext,
    compose_messages,
)
from backend.session import RobotSession

logger = logging.getLogger(__name__)


class TurnSink(Protocol):
    """Where a turn's effects go.

    The WebSocket connection implements this; so does an in-memory recorder in the
    tests. The pipeline cannot tell them apart, which is the point.
    """

    async def set_state(self, state: SystemState) -> None: ...
    async def set_expression(self, expression: Expression, hold_ms: int) -> None: ...
    async def speak_start(self, turn_id: int, sample_rate: int) -> None: ...
    async def speak_audio(self, pcm: bytes) -> None: ...
    async def speak_end(self, turn_id: int) -> None: ...
    async def report_error(self, code: str, message: str) -> None: ...


@dataclass(frozen=True)
class TurnResult:
    """What happened in one turn. Returned for logging and for the tests to assert on."""

    turn_id: int
    reply: AgentReply
    transcript: str
    language: str | None
    error: ErrorCategory | None
    audio_bytes: int
    latency: dict[str, int | None]

    @property
    def ok(self) -> bool:
        return self.error is None


class ConversationPipeline:
    """Runs turns for any session. Stateless between turns; state lives in the session."""

    def __init__(self, providers: ProviderBundle, settings: Settings) -> None:
        self._providers = providers
        self._settings = settings
        self._deployment = DeploymentContext(
            robot_name=settings.robot_name,
            venue=settings.deployment_venue,
            notes=settings.deployment_notes,
        )

    # -- public entry point --------------------------------------------------------

    async def run_turn(self, session: RobotSession, pcm: bytes, sink: TurnSink) -> TurnResult:
        """Run one complete turn for `session` over the utterance in `pcm`.

        Never raises for an expected failure — STT/LLM/TTS errors, timeouts and
        cancellation all resolve into a `TurnResult` with an `error` set and the robot
        back in a usable state. Only a genuine programming bug escapes.
        """
        turn_id = session.begin_turn()
        metrics = session.metrics
        assert metrics is not None  # begin_turn always sets it

        log_event(
            logger,
            logging.INFO,
            "turn started",
            session_id=session.session_id,
            device_id=session.device_id,
            turn_id=turn_id,
            role=session.role.id,
            audio_bytes=len(pcm),
            audio_ms=pcm_duration_ms(pcm),
        )

        try:
            transcript = await self._run_stt(session, pcm, sink)
            reply = await self._run_llm(session, transcript, sink)
            audio_bytes = await self._run_tts(session, reply, transcript.language, sink)
        except _TurnFailure as failure:
            return await self._fail(session, sink, failure, turn_id)
        except TurnCancelled:
            return await self._cancelled(session, sink, turn_id)

        session.record_exchange(transcript.text, reply.speech)
        session.trim_history(
            max_turns=self._settings.max_history_turns,
            max_chars=self._settings.max_history_chars,
        )

        latency = metrics.summary()
        log_event(
            logger,
            logging.INFO,
            "turn completed",
            session_id=session.session_id,
            turn_id=turn_id,
            language=transcript.language,
            expression=reply.emotion.value,
            transcript=transcript.text,
            reply=reply.speech,
            **{k: v for k, v in latency.items()},
        )
        return TurnResult(
            turn_id=turn_id,
            reply=reply,
            transcript=transcript.text,
            language=transcript.language,
            error=None,
            audio_bytes=audio_bytes,
            latency=latency,
        )

    # -- stages ----------------------------------------------------------------------

    async def _run_stt(self, session: RobotSession, pcm: bytes, sink: TurnSink) -> Transcript:
        self._check_cancelled(session)
        session.transition(SystemState.PROCESSING)
        await sink.set_state(SystemState.PROCESSING)

        try:
            async with asyncio.timeout(self._settings.stt_timeout_s):
                transcript = await self._providers.stt.transcribe(
                    pcm,
                    sample_rate=AUDIO_SAMPLE_RATE,
                    language_hint=tuple(session.language_hint()),
                )
        except asyncio.TimeoutError as exc:
            raise _TurnFailure(ErrorCategory.STT_TIMEOUT, "STT timed out", None) from exc
        except ProviderError as exc:
            raise _TurnFailure(ErrorCategory.STT_FAILED, str(exc), None) from exc

        if session.metrics:
            session.metrics.stt_done_at = time.monotonic()

        if transcript.is_empty:
            # Not an error condition in the technical sense — a cough, a door, a false
            # VAD trigger. It still needs a spoken response, or the robot just sits
            # there having visibly heard something.
            raise _TurnFailure(
                ErrorCategory.STT_EMPTY, "empty transcript", transcript.language
            )
        return transcript

    async def _run_llm(
        self, session: RobotSession, transcript: Transcript, sink: TurnSink
    ) -> AgentReply:
        self._check_cancelled(session)
        session.transition(SystemState.THINKING)
        await sink.set_state(SystemState.THINKING)

        messages = compose_messages(
            session.role,
            session.history,
            transcript.text,
            prompts_dir=self._settings.prompts_dir,
            deployment=self._deployment,
            session=SessionContext(
                turn_index=session.turn_count,
                detected_language=transcript.language,
                is_first_turn=session.turn_count == 0,
            ),
        )

        try:
            async with asyncio.timeout(self._settings.llm_timeout_s):
                result = await self._providers.llm.generate(
                    messages,
                    temperature=self._settings.llm_temperature,
                    max_output_tokens=self._settings.llm_max_output_tokens,
                )
        except asyncio.TimeoutError as exc:
            raise _TurnFailure(
                ErrorCategory.LLM_TIMEOUT, "LLM timed out", transcript.language
            ) from exc
        except ProviderError as exc:
            raise _TurnFailure(
                ErrorCategory.LLM_FAILED, str(exc), transcript.language
            ) from exc

        if session.metrics:
            session.metrics.llm_done_at = time.monotonic()

        reply = parse_agent_output(result.text)
        if reply.is_empty:
            raise _TurnFailure(
                ErrorCategory.LLM_EMPTY, "model returned no usable text", transcript.language
            )

        # The role, not the model, has the final say on which faces exist for this
        # deployment. A tutor configured without `sleepy` never looks bored.
        return AgentReply(
            speech=reply.speech,
            emotion=session.role.coerce_expression(reply.emotion),
            metadata={"model": result.model, **reply.metadata},
        )

    async def _run_tts(
        self,
        session: RobotSession,
        reply: AgentReply,
        language: str | None,
        sink: TurnSink,
    ) -> int:
        self._check_cancelled(session)

        # The expression is sent *before* the audio so the face is already right when the
        # first word lands. A face that catches up half a second later reads as a lag.
        await sink.set_expression(reply.emotion, DEFAULT_EXPRESSION_HOLD_MS)
        session.pending_expression = reply.emotion

        session.transition(SystemState.SPEAKING)
        await sink.set_state(SystemState.SPEAKING)
        await sink.speak_start(session.turn_id, AUDIO_SAMPLE_RATE)

        total = 0
        turn_id = session.turn_id
        try:
            async with asyncio.timeout(self._settings.tts_timeout_s):
                stream = self._providers.tts.synthesize(
                    reply.speech,
                    sample_rate=AUDIO_SAMPLE_RATE,
                    voice=session.role.voice.voice or None,
                    speed=session.role.voice.speed,
                    language=language,
                )
                async for chunk in stream:
                    # Re-check every chunk: a cancel during a long reply should stop the
                    # audio mid-sentence, which is what a person expects when they
                    # interrupt.
                    if not session.is_current_turn(turn_id):
                        raise TurnCancelled("cancelled during synthesis")
                    if session.metrics:
                        now = time.monotonic()
                        if session.metrics.tts_first_chunk_at is None:
                            session.metrics.tts_first_chunk_at = now
                            session.metrics.first_audio_sent_at = now
                    await sink.speak_audio(chunk.pcm)
                    total += len(chunk.pcm)
        except asyncio.TimeoutError as exc:
            await sink.speak_end(turn_id)
            raise _TurnFailure(ErrorCategory.TTS_TIMEOUT, "TTS timed out", language) from exc
        except ProviderError as exc:
            await sink.speak_end(turn_id)
            raise _TurnFailure(ErrorCategory.TTS_FAILED, str(exc), language) from exc

        await sink.speak_end(turn_id)
        if session.metrics:
            session.metrics.audio_bytes = total
        return total

    # -- failure handling ---------------------------------------------------------------

    async def _fail(
        self, session: RobotSession, sink: TurnSink, failure: "_TurnFailure", turn_id: int
    ) -> TurnResult:
        """Speak a social apology, log the technical detail, return to a usable state."""
        log_event(
            logger,
            logging.WARNING,
            "turn failed",
            session_id=session.session_id,
            turn_id=turn_id,
            error_category=failure.category.value,
            error_detail=failure.detail,
        )
        await sink.report_error(failure.category.value, failure.detail)

        speech = fallback_speech(failure.category, failure.language)
        expression = fallback_expression(failure.category)
        reply = AgentReply(speech=speech, emotion=session.role.coerce_expression(expression))

        audio_bytes = 0
        # The apology itself goes through TTS, but a TTS failure must not recurse: if
        # the voice is what broke, the robot shows a confused face and stays quiet.
        if failure.category not in (ErrorCategory.TTS_FAILED, ErrorCategory.TTS_TIMEOUT):
            try:
                audio_bytes = await self._speak_fallback(session, reply, failure.language, sink)
            except Exception as exc:  # noqa: BLE001 - last line of defence
                log_event(
                    logger,
                    logging.ERROR,
                    "failed to speak fallback",
                    session_id=session.session_id,
                    turn_id=turn_id,
                    error_detail=str(exc),
                )
        else:
            await sink.set_expression(expression, DEFAULT_EXPRESSION_HOLD_MS)

        await self._return_to_listening(session, sink)
        latency = session.metrics.summary() if session.metrics else {}
        return TurnResult(
            turn_id=turn_id,
            reply=reply,
            transcript="",
            language=failure.language,
            error=failure.category,
            audio_bytes=audio_bytes,
            latency=latency,
        )

    async def _speak_fallback(
        self, session: RobotSession, reply: AgentReply, language: str | None, sink: TurnSink
    ) -> int:
        """Synthesise and send the apology, with its own (shorter) budget."""
        await sink.set_expression(reply.emotion, DEFAULT_EXPRESSION_HOLD_MS)
        session.force_state(SystemState.SPEAKING)
        await sink.set_state(SystemState.SPEAKING)
        await sink.speak_start(session.turn_id, AUDIO_SAMPLE_RATE)
        total = 0
        try:
            async with asyncio.timeout(self._settings.tts_timeout_s):
                async for chunk in self._providers.tts.synthesize(
                    reply.speech,
                    sample_rate=AUDIO_SAMPLE_RATE,
                    voice=session.role.voice.voice or None,
                    speed=session.role.voice.speed,
                    language=language,
                ):
                    await sink.speak_audio(chunk.pcm)
                    total += len(chunk.pcm)
        finally:
            # Always close the speak window, even if synthesis died halfway: an
            # unterminated speak_start leaves the device waiting for audio forever.
            await sink.speak_end(session.turn_id)
        return total

    async def _cancelled(
        self, session: RobotSession, sink: TurnSink, turn_id: int
    ) -> TurnResult:
        log_event(
            logger,
            logging.INFO,
            "turn cancelled",
            session_id=session.session_id,
            turn_id=turn_id,
        )
        await self._return_to_listening(session, sink)
        return TurnResult(
            turn_id=turn_id,
            reply=AgentReply(speech=""),
            transcript="",
            language=None,
            error=ErrorCategory.CANCELLED,
            audio_bytes=0,
            latency={},
        )

    async def _return_to_listening(self, session: RobotSession, sink: TurnSink) -> None:
        """The invariant: however a turn ends, the robot ends up able to hear again."""
        session.force_state(SystemState.LISTENING)
        await sink.set_state(SystemState.LISTENING)

    @staticmethod
    def _check_cancelled(session: RobotSession) -> None:
        if session.cancelled:
            raise TurnCancelled("turn cancelled before stage")


@dataclass
class _TurnFailure(Exception):
    """Internal control-flow exception carrying everything `_fail` needs."""

    category: ErrorCategory
    detail: str
    language: str | None
