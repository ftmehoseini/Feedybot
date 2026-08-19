"""Typed agent reply tests.

The three invariants: markers never reach TTS, unknown emotions become neutral, and
empty output is detectable rather than silently spoken.
"""

from __future__ import annotations

import pytest

from backend.agent_reply import AgentReply, parse_agent_output
from backend.emotion import Expression


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Hello!\n#emotion: happy", Expression.HAPPY),
        ("Hello!\n#emotion:happy", Expression.HAPPY),
        ("Hello!\n#emotion: HAPPY", Expression.HAPPY),
        ("Hello!\n  #emotion: curious  ", Expression.CURIOUS),
        ("Hello!\nemotion: confused", Expression.CONFUSED),
        ("Hello!\n#emotion = surprised", Expression.SURPRISED),
        ("Hello! (#emotion: encouraging)", Expression.ENCOURAGING),
        ("Hello! [#emotion: sleepy]", Expression.SLEEPY),
    ],
)
def test_emotion_marker_variants_are_all_parsed(raw: str, expected: Expression) -> None:
    reply = parse_agent_output(raw)
    assert reply.emotion is expected
    # And in every variant, the marker is gone from what will be spoken.
    assert "emotion" not in reply.speech.lower()


@pytest.mark.parametrize(
    "raw",
    [
        "Hi\n#emotion: ecstatic",
        "Hi\n#emotion: ",
        "Hi\n#emotion: 12345",
        "Hi",
    ],
)
def test_unknown_or_missing_emotion_falls_back_to_neutral(raw: str) -> None:
    assert parse_agent_output(raw).emotion is Expression.NEUTRAL


def test_marker_is_never_left_in_speech() -> None:
    """The single most important invariant: TTS must never read a marker aloud."""
    reply = parse_agent_output("That's great news!\n#emotion: happy")
    assert reply.speech == "That's great news!"


@pytest.mark.parametrize("raw", [None, "", "   ", "\n\n", "#emotion: happy"])
def test_empty_output_is_reported_not_spoken(raw: str | None) -> None:
    reply = parse_agent_output(raw)
    assert reply.is_empty


def test_markdown_is_stripped_because_it_would_be_pronounced() -> None:
    reply = parse_agent_output("**Really** important `code` and ## a heading\n#emotion: neutral")
    assert "*" not in reply.speech
    assert "`" not in reply.speech
    assert "#" not in reply.speech
    assert "Really important code" in reply.speech


def test_persian_text_survives_parsing_intact() -> None:
    reply = parse_agent_output("سلام! حالت چطوره؟\n#emotion: happy")
    assert reply.speech == "سلام! حالت چطوره؟"
    assert reply.emotion is Expression.HAPPY


def test_sentence_punctuation_is_preserved_for_prosody() -> None:
    reply = parse_agent_output("Wait, really? That's wild!\n#emotion: surprised")
    assert reply.speech == "Wait, really? That's wild!"


def test_multiline_speech_keeps_its_lines() -> None:
    reply = parse_agent_output("First thought.\nSecond thought.\n#emotion: curious")
    assert reply.speech == "First thought.\nSecond thought."


def test_prose_containing_the_word_emotion_is_not_mangled() -> None:
    """A reply *about* emotions must not lose half its text to the marker regex."""
    reply = parse_agent_output(
        "Emotions are complicated, aren't they?\n#emotion: curious"
    )
    assert reply.speech == "Emotions are complicated, aren't they?"
    assert reply.emotion is Expression.CURIOUS


def test_reply_is_immutable() -> None:
    reply = AgentReply(speech="hi")
    with pytest.raises(Exception):
        reply.speech = "changed"  # type: ignore[misc]


def test_metadata_defaults_to_an_independent_dict() -> None:
    first = AgentReply(speech="a")
    second = AgentReply(speech="b")
    first.metadata["x"] = 1
    assert second.metadata == {}
