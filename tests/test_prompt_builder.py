"""Prompt composition tests.

Layer order is a contract, not a preference: the core robot rules must come first and
appear exactly once, and no layer may leak content from a role that is not loaded.
"""

from __future__ import annotations

import pytest

from backend.roles.prompt_builder import (
    DeploymentContext,
    PromptLayer,
    SessionContext,
    build_system_prompt,
    compose_messages,
)
from backend.roles.schema import RolePack

from .conftest import PROMPTS_DIR


def build(role: RolePack, **kwargs) -> str:
    return build_system_prompt(role, prompts_dir=PROMPTS_DIR, **kwargs)


def test_core_rules_come_first_and_exactly_once(companion_role: RolePack) -> None:
    prompt = build(companion_role)
    assert prompt.count("# Core rules") == 1
    assert prompt.index("# Core rules") < prompt.index("# Your role")


def test_layers_appear_in_declared_order(companion_role: RolePack) -> None:
    prompt = build(
        companion_role,
        deployment=DeploymentContext(robot_name="Fafobot", venue="a test bench"),
        session=SessionContext(turn_index=2, detected_language="en", is_first_turn=False),
    )
    positions = [
        prompt.index("# Core rules"),
        prompt.index("# Your role"),
        prompt.index("# This deployment"),
        prompt.index("# Right now"),
    ]
    assert positions == sorted(positions)


def test_core_rules_state_the_hardware_the_robot_actually_has(
    companion_role: RolePack,
) -> None:
    """The honesty rules are the product's credibility. Assert they are present."""
    prompt = build(companion_role).lower()
    assert "no camera" in prompt
    assert "cannot move" in prompt
    assert "no markdown" in prompt


def test_role_content_reaches_the_prompt(teacher_role: RolePack) -> None:
    prompt = build(teacher_role)
    assert "patient English conversation partner" in prompt
    # The teacher's language policy must be stated, not implied.
    assert "Reply in English by default" in prompt
    assert teacher_role.languages.guidance.split(".")[0][:40] in prompt


def test_language_policy_differs_between_roles(
    teacher_role: RolePack, companion_role: RolePack
) -> None:
    companion_prompt = build(companion_role)
    teacher_prompt = build(teacher_role)
    assert "Reply in whichever of those languages the person used" in companion_prompt
    assert "Reply in whichever of those languages the person used" not in teacher_prompt
    assert "Reply in English by default" in teacher_prompt


def test_switching_roles_leaks_no_content_from_the_other(
    teacher_role: RolePack, companion_role: RolePack
) -> None:
    """A new role must produce a prompt with no trace of the previous one."""
    teacher_prompt = build(teacher_role)
    companion_prompt = build(companion_role)

    # Distinctive phrases from each role's YAML.
    assert "learner" in teacher_prompt.lower()
    assert "learner" not in companion_prompt.lower()
    assert "desk robot" in companion_prompt.lower()
    assert "desk robot" not in teacher_prompt.lower()


def test_only_allowed_expressions_are_offered_to_the_model(
    teacher_role: RolePack, companion_role: RolePack
) -> None:
    teacher_prompt = build(teacher_role)
    assert "encouraging" in teacher_prompt
    # The teacher role does not allow `sleepy`, so the model must never be shown it as
    # an option.
    allowed_line = [
        line for line in teacher_prompt.splitlines() if "must be exactly one of" in line
    ][0]
    assert "sleepy" not in allowed_line

    companion_line = [
        line for line in build(companion_role).splitlines() if "must be exactly one of" in line
    ][0]
    assert "sleepy" in companion_line


def test_first_turn_and_later_turns_differ(companion_role: RolePack) -> None:
    first = build(companion_role, session=SessionContext(is_first_turn=True))
    later = build(
        companion_role, session=SessionContext(turn_index=5, is_first_turn=False)
    )
    assert "start of the conversation" in first or "first thing you say" in first
    assert "5 turns into this conversation" in later


def test_detected_language_is_reported_to_the_model(companion_role: RolePack) -> None:
    prompt = build(companion_role, session=SessionContext(detected_language="fa"))
    assert "just spoke Persian" in prompt


def test_no_unimplemented_layers_are_emitted(companion_role: RolePack) -> None:
    """Layers 7-9 are reserved. Nothing may claim retrieval, tools, or memory exist."""
    prompt = build(companion_role).lower()
    for forbidden in ("retrieved knowledge", "tool results", "long-term memory"):
        assert forbidden not in prompt
    # And the reserved slots are declared but unused.
    assert PromptLayer.RETRIEVED_KNOWLEDGE > PromptLayer.USER_CONTEXT


def test_user_context_layer_is_optional_and_ordered(companion_role: RolePack) -> None:
    without = build(companion_role)
    assert "# About this person" not in without

    with_context = build(companion_role, user_context="They mentioned they like trains.")
    assert "# About this person" in with_context
    assert with_context.index("# Right now") < with_context.index("# About this person")


def test_compose_messages_puts_history_between_system_and_user(
    companion_role: RolePack,
) -> None:
    history = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]
    messages = compose_messages(
        companion_role, history, "second question", prompts_dir=PROMPTS_DIR
    )
    assert messages[0]["role"] == "system"
    assert messages[1]["content"] == "first question"
    assert messages[2]["content"] == "first answer"
    assert messages[-1] == {"role": "user", "content": "second question"}


def test_compose_messages_does_not_flatten_history_into_the_system_prompt(
    companion_role: RolePack,
) -> None:
    history = [{"role": "user", "content": "a distinctive earlier remark"}]
    messages = compose_messages(companion_role, history, "now", prompts_dir=PROMPTS_DIR)
    assert "a distinctive earlier remark" not in messages[0]["content"]


def test_missing_core_prompt_is_a_config_error(companion_role: RolePack, tmp_path) -> None:
    from backend.errors import ConfigError

    with pytest.raises(ConfigError, match="core prompt not found"):
        build_system_prompt(companion_role, prompts_dir=tmp_path)
