"""Conversation pipeline tests.

This file exists to hold the reliability promises to account. Every failure mode the
brief names has a test: STT/LLM/TTS failure, timeout at each stage, cancellation, and
empty output. In every one the assertion is the same — **the robot says something human
and ends up able to listen again.**
"""

from __future__ import annotations

import asyncio

import pytest

from backend.audio import tone
from backend.config import Settings
from backend.emotion import Expression, SystemState
from backend.errors import ErrorCategory
from backend.pipeline import ConversationPipeline
from backend.providers.llm.fake import FakeLLMProvider
from backend.providers.stt.fake import FakeSTTProvider
from backend.providers.tts.fake import FakeTTSProvider
from backend.session import RobotSession

from .conftest import RecordingSink, make_pipeline

UTTERANCE = tone(1200, frequency_hz=200.0)


async def test_english_turn_completes(
    pipeline: ConversationPipeline, session: RobotSession, sink: RecordingSink
) -> None:
    result = await pipeline.run_turn(session, UTTERANCE, sink)

    assert result.ok
    assert result.language == "en"
    assert result.audio_bytes > 0
    assert sink.audio, "the robot must actually produce speech audio"
    assert sink.speak_starts == [1] and sink.speak_ends == [1]
    # The pipeline hands off while the robot is still speaking. Returning to LISTENING
    # is the connection layer's job, because only it sees `playback_done`
    # (see test_connection.py::test_playback_done_returns_the_robot_to_listening).
    assert session.state is SystemState.SPEAKING


async def test_persian_turn_completes(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    pipeline = make_pipeline(settings, stt=FakeSTTProvider(["سلام، حالت چطوره؟"]))
    result = await pipeline.run_turn(session, UTTERANCE, sink)

    assert result.ok
    assert result.language == "fa"
    assert session.state is SystemState.SPEAKING


async def test_state_sequence_is_the_documented_one(
    pipeline: ConversationPipeline, session: RobotSession, sink: RecordingSink
) -> None:
    await pipeline.run_turn(session, UTTERANCE, sink)
    assert sink.states == [
        SystemState.PROCESSING,
        SystemState.THINKING,
        SystemState.SPEAKING,
    ]


async def test_expression_is_sent_before_the_audio(
    pipeline: ConversationPipeline, session: RobotSession, sink: RecordingSink
) -> None:
    """A face that catches up after the first word reads as lag."""
    await pipeline.run_turn(session, UTTERANCE, sink)
    assert sink.timeline.index("expression:happy") < sink.timeline.index("speak_start")


async def test_history_grows_and_stays_bounded(
    pipeline: ConversationPipeline, session: RobotSession, sink: RecordingSink
) -> None:
    for _ in range(8):
        await pipeline.run_turn(session, UTTERANCE, sink)
    # settings fixture sets max_history_turns=4.
    assert session.turn_count == 4
    assert session.turn_id == 8


async def test_role_clamps_an_expression_the_model_asked_for(
    settings: Settings, teacher_role, sink: RecordingSink
) -> None:
    """The role, not the model, decides which faces this deployment has."""
    session = RobotSession(role=teacher_role)
    session.force_state(SystemState.LISTENING)
    pipeline = make_pipeline(settings, llm=FakeLLMProvider(["Nice work.\n#emotion: sleepy"]))

    result = await pipeline.run_turn(session, UTTERANCE, sink)
    assert result.reply.emotion is Expression.ENCOURAGING
    assert result.reply.speech == "Nice work."


async def test_emotion_marker_never_reaches_the_speech_synthesiser(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    tts = FakeTTSProvider()
    pipeline = make_pipeline(
        settings, llm=FakeLLMProvider(["All good.\n#emotion: happy"]), tts=tts
    )
    await pipeline.run_turn(session, UTTERANCE, sink)
    assert tts.last_text == "All good."
    assert "emotion" not in tts.last_text.lower()


# ---------------------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------------------


async def test_stt_failure_speaks_an_apology_and_recovers(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    pipeline = make_pipeline(settings, stt=FakeSTTProvider(fail_after=0))
    result = await pipeline.run_turn(session, UTTERANCE, sink)

    assert result.error is ErrorCategory.STT_FAILED
    assert result.reply.speech and "500" not in result.reply.speech
    assert sink.audio, "the apology must be spoken, not swallowed"
    assert session.state is SystemState.LISTENING


async def test_empty_transcript_asks_the_person_to_repeat(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    pipeline = make_pipeline(settings, stt=FakeSTTProvider([""]))
    result = await pipeline.run_turn(session, UTTERANCE, sink)

    assert result.error is ErrorCategory.STT_EMPTY
    assert sink.audio
    assert session.state is SystemState.LISTENING


async def test_llm_failure_recovers(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    pipeline = make_pipeline(settings, llm=FakeLLMProvider(fail_after=0))
    result = await pipeline.run_turn(session, UTTERANCE, sink)

    assert result.error is ErrorCategory.LLM_FAILED
    assert sink.audio
    assert session.state is SystemState.LISTENING


async def test_empty_model_output_recovers(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    pipeline = make_pipeline(settings, llm=FakeLLMProvider(["   \n#emotion: happy"]))
    result = await pipeline.run_turn(session, UTTERANCE, sink)

    assert result.error is ErrorCategory.LLM_EMPTY
    assert session.state is SystemState.LISTENING


async def test_tts_failure_shows_a_face_and_stays_silent(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    """When the voice is what broke, do not recurse into it trying to apologise."""
    pipeline = make_pipeline(settings, tts=FakeTTSProvider(fail=True))
    result = await pipeline.run_turn(session, UTTERANCE, sink)

    assert result.error is ErrorCategory.TTS_FAILED
    assert not sink.audio
    assert Expression.CONFUSED in [expression for expression, _ in sink.expressions]
    assert session.state is SystemState.LISTENING
    # An unterminated speak window would leave the device waiting for audio forever.
    assert len(sink.speak_ends) == len(sink.speak_starts)


@pytest.mark.parametrize(
    "provider_kwargs, expected",
    [
        ({"stt": FakeSTTProvider(delay_s=5.0)}, ErrorCategory.STT_TIMEOUT),
        ({"llm": FakeLLMProvider(delay_s=5.0)}, ErrorCategory.LLM_TIMEOUT),
        ({"tts": FakeTTSProvider(delay_s=5.0)}, ErrorCategory.TTS_TIMEOUT),
    ],
)
async def test_every_stage_times_out_rather_than_hanging(
    settings: Settings,
    session: RobotSession,
    sink: RecordingSink,
    provider_kwargs: dict,
    expected: ErrorCategory,
) -> None:
    """No path may leave the robot in THINKING or SPEAKING forever."""
    pipeline = make_pipeline(settings, **provider_kwargs)
    result = await asyncio.wait_for(pipeline.run_turn(session, UTTERANCE, sink), timeout=10)

    assert result.error is expected
    assert session.state is SystemState.LISTENING


async def test_cancel_during_recognition_stops_before_the_model_is_called(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    """A long press while the robot is thinking must not still cost an LLM call."""
    llm = FakeLLMProvider()
    pipeline = make_pipeline(settings, stt=FakeSTTProvider(delay_s=0.3), llm=llm)

    task = asyncio.create_task(pipeline.run_turn(session, UTTERANCE, sink))
    await asyncio.sleep(0.05)
    session.cancel_turn()
    result = await task

    assert result.error is ErrorCategory.CANCELLED
    assert llm.calls == 0, "a cancelled turn must not reach the model"
    assert session.state is SystemState.LISTENING
    assert not sink.audio


async def test_cancel_during_synthesis_stops_the_audio_mid_reply(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    """Interrupting a speaking robot should stop it, not just hide the rest."""
    pipeline = make_pipeline(settings, tts=FakeTTSProvider(chunk_delay_s=0.02))

    task = asyncio.create_task(pipeline.run_turn(session, UTTERANCE, sink))
    await asyncio.sleep(0.06)
    session.cancel_turn()
    result = await task

    assert result.error is ErrorCategory.CANCELLED
    assert session.state is SystemState.LISTENING
    # Some audio went out before the cancel; the rest never did.
    full_reply_bytes = 38_720
    assert 0 < len(sink.audio) < full_reply_bytes


async def test_a_failed_turn_is_not_recorded_in_history(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    """The apology is not something the person said, and must not become context."""
    pipeline = make_pipeline(settings, llm=FakeLLMProvider(fail_after=0))
    await pipeline.run_turn(session, UTTERANCE, sink)
    assert session.history == []


async def test_technical_detail_goes_to_the_error_channel_not_the_speech(
    settings: Settings, session: RobotSession, sink: RecordingSink
) -> None:
    pipeline = make_pipeline(settings, llm=FakeLLMProvider(fail_after=0))
    result = await pipeline.run_turn(session, UTTERANCE, sink)

    assert sink.errors, "the device log must receive the technical reason"
    code, message = sink.errors[0]
    assert code == ErrorCategory.LLM_FAILED.value
    assert "fake LLM failure" in message
    # ...and none of it appears in what the human hears.
    assert "fake LLM failure" not in result.reply.speech


async def test_latency_is_measured_for_every_stage(
    pipeline: ConversationPipeline, session: RobotSession, sink: RecordingSink
) -> None:
    result = await pipeline.run_turn(session, UTTERANCE, sink)
    latency = result.latency
    for key in ("stt_ms", "llm_ms", "tts_first_audio_ms", "time_to_first_audio_ms"):
        assert latency[key] is not None, f"{key} was not measured"
        assert latency[key] >= 0
    # total_ms needs the device's playback_done, which the pipeline never sees.
    assert latency["total_ms"] is None


async def test_two_turns_do_not_interleave(
    pipeline: ConversationPipeline, session: RobotSession
) -> None:
    """Half-duplex means one turn at a time, enforced by the session lock."""
    first_sink = RecordingSink()
    second_sink = RecordingSink()

    async def run(sink: RecordingSink) -> None:
        async with session.turn_lock:
            await pipeline.run_turn(session, UTTERANCE, sink)

    await asyncio.gather(run(first_sink), run(second_sink))
    assert len(first_sink.speak_starts) == 1
    assert len(second_sink.speak_starts) == 1
    assert first_sink.speak_starts != second_sink.speak_starts


async def test_role_is_the_only_difference_between_deployments(
    settings: Settings, companion_role, teacher_role, sink: RecordingSink
) -> None:
    """Same pipeline, same providers, two roles: only the prompt differs."""
    llm = FakeLLMProvider()
    pipeline = make_pipeline(settings, llm=llm)

    companion_session = RobotSession(role=companion_role)
    companion_session.force_state(SystemState.LISTENING)
    await pipeline.run_turn(companion_session, UTTERANCE, sink)
    companion_prompt = llm.last_messages[0]["content"]

    teacher_session = RobotSession(role=teacher_role)
    teacher_session.force_state(SystemState.LISTENING)
    await pipeline.run_turn(teacher_session, UTTERANCE, RecordingSink())
    teacher_prompt = llm.last_messages[0]["content"]

    assert companion_prompt != teacher_prompt
    # The core rules are identical in both; only the role layer changed.
    marker = "# Core rules"
    assert companion_prompt.count(marker) == teacher_prompt.count(marker) == 1
