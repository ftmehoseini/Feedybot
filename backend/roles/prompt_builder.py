"""Layered prompt composition.

The system prompt is assembled from ordered layers rather than written as one string.
That matters for a reason beyond tidiness: when a deployment misbehaves, you need to
know *which layer* said the wrong thing. A single 2,000-word literal makes that
impossible to answer.

Layer order is fixed and asserted by the tests:

    1. ROBOT CORE RULES      always present, exactly once, first
    2. ROLE PACK             per-deployment behaviour
    3. DEPLOYMENT CONFIG     venue, robot name, operator notes
    4. SESSION CONTEXT       turn count, detected language
    5. USER CONTEXT          reserved; empty in V1
    6. CONVERSATION HISTORY  supplied as chat messages, not as prompt text

Layers 7-9 (retrieved knowledge, tool results, long-term memory) are named here as
reserved slots so the ordering question is already settled when they arrive. They are
not implemented, and `build_system_prompt` never emits them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from backend.errors import ConfigError
from backend.roles.schema import RolePack

CORE_PROMPT_FILENAME = "core_robot.md"

_LANGUAGE_NAMES = {"fa": "Persian", "en": "English"}


class PromptLayer(IntEnum):
    """Composition order. Lower numbers appear earlier in the prompt."""

    CORE = 1
    ROLE = 2
    DEPLOYMENT = 3
    SESSION = 4
    USER_CONTEXT = 5
    # -- reserved, not implemented in V1 --------------------------------------------
    RETRIEVED_KNOWLEDGE = 7
    TOOL_RESULTS = 8
    LONG_TERM_MEMORY = 9


@dataclass(frozen=True)
class DeploymentContext:
    """Facts about this particular installation, independent of the role."""

    robot_name: str = "Fafobot"
    venue: str = ""
    notes: str = ""


@dataclass(frozen=True)
class SessionContext:
    """Facts about the conversation happening right now."""

    turn_index: int = 0
    detected_language: str | None = None
    is_first_turn: bool = True


@lru_cache(maxsize=4)
def _read_core_prompt(prompts_dir: str) -> str:
    path = Path(prompts_dir) / CORE_PROMPT_FILENAME
    if not path.is_file():
        raise ConfigError(f"core prompt not found at {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ConfigError(f"core prompt at {path} is empty")
    return text


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items if str(item).strip())


def _role_section(role: RolePack) -> str:
    """Render the Role Pack as prompt text.

    Every branch here is driven by role *data*. There is no `if role.id == ...`
    anywhere in this function, and adding a role must never require editing it.
    """
    parts: list[str] = [
        "# Your role",
        "",
        f"You are {role.identity.name}, a {role.identity.role}.",
    ]
    if role.identity.description:
        parts.append(role.identity.description)

    if role.objective:
        parts += ["", "## What you are trying to do", "", _bullets(role.objective)]

    # -- language policy ------------------------------------------------------------
    languages = role.languages
    spoken = ", ".join(_LANGUAGE_NAMES.get(code, code) for code in languages.primary)
    lang_lines = [f"You understand and can speak {spoken}."]
    if languages.policy == "mirror":
        lang_lines.append(
            "Reply in whichever of those languages the person used. If they switch, "
            "switch with them without commenting on it."
        )
    else:
        preferred = _LANGUAGE_NAMES.get(
            languages.preferred_language or "", languages.preferred_language or ""
        )
        lang_lines.append(
            f"Reply in {preferred} by default, even when the person speaks another "
            f"language you understand. Understanding them is not a reason to switch."
        )
    if languages.guidance:
        lang_lines.append(languages.guidance)
    parts += ["", "## Language", "", "\n".join(lang_lines)]

    # -- conversational shape --------------------------------------------------------
    style = role.conversation
    style_lines = [
        f"Keep replies to at most {style.max_response_sentences} "
        f"{'sentence' if style.max_response_sentences == 1 else 'sentences'}."
    ]
    if style.ask_followup_questions:
        style_lines.append(
            "Usually end with a short question that gives the person somewhere to go."
        )
    else:
        style_lines.append("Answer and stop. Do not fish for another turn.")
    if style.avoid_monologues:
        style_lines.append("Never deliver a monologue. Conversation is a back and forth.")
    if style.notes:
        style_lines.append(_bullets(style.notes))
    parts += ["", "## How you talk", "", "\n".join(style_lines)]

    # -- personality -----------------------------------------------------------------
    personality = role.personality
    parts += [
        "",
        "## Character",
        "",
        _bullets(
            [
                f"Warmth: {personality.warmth}.",
                f"Humour: {personality.humor}.",
                f"Curiosity: {personality.curiosity}.",
                f"Formality: {personality.formality}.",
            ]
        ),
    ]

    # -- safety ----------------------------------------------------------------------
    safety_rules: list[str] = []
    if safety := role.safety:
        if safety.never_claim_unavailable_sensors:
            safety_rules.append(
                "Never claim to sense anything you have no hardware for — no sight, no "
                "touch, no temperature, no location."
            )
        if safety.never_claim_physical_actions_not_supported:
            safety_rules.append(
                "Never claim to have performed a physical action. You cannot move."
            )
        for topic in safety.avoid_topics:
            safety_rules.append(f"Do not give {topic}. Say it is outside what you do, and move on.")
        safety_rules += list(safety.additional_rules)
    if safety_rules:
        parts += ["", "## Limits", "", _bullets(safety_rules)]

    # -- emotion vocabulary ------------------------------------------------------------
    allowed = ", ".join(e.value for e in role.emotion_policy.allowed)
    parts += [
        "",
        "## Your allowed expressions",
        "",
        f"The emotion marker on your final line must be exactly one of: {allowed}.",
        f"When in doubt use {role.emotion_policy.resting.value}.",
    ]
    return "\n".join(parts).strip()


def _deployment_section(deployment: DeploymentContext) -> str:
    lines = [f"You are installed as \"{deployment.robot_name}\"."]
    if deployment.venue:
        lines.append(f"You are located at: {deployment.venue}.")
    if deployment.notes:
        lines.append(deployment.notes)
    if len(lines) == 1 and not deployment.venue and not deployment.notes:
        return ""
    return "# This deployment\n\n" + "\n".join(lines)


def _session_section(session: SessionContext, role: RolePack) -> str:
    lines: list[str] = []
    if session.is_first_turn:
        if role.session.greeting:
            if role.session.greeting_text:
                lines.append(
                    "This is the first thing you say in this conversation. Greet them "
                    f"along these lines, in your own words: \"{role.session.greeting_text}\""
                )
            else:
                lines.append(
                    "This is the start of the conversation. Open with a short, warm "
                    "greeting before anything else."
                )
    else:
        lines.append(f"You are {session.turn_index} turns into this conversation.")
    if session.detected_language:
        name = _LANGUAGE_NAMES.get(session.detected_language, session.detected_language)
        lines.append(f"The person just spoke {name}.")
    if role.memory.mode == "session_only":
        lines.append(
            "You remember this conversation only. When it ends, it is gone, and you "
            "should not imply otherwise."
        )
    if not lines:
        return ""
    return "# Right now\n\n" + "\n".join(lines)


def build_system_prompt(
    role: RolePack,
    *,
    prompts_dir: Path,
    deployment: DeploymentContext | None = None,
    session: SessionContext | None = None,
    user_context: str = "",
) -> str:
    """Compose the full system prompt for one turn.

    Args:
        role: the active Role Pack.
        prompts_dir: directory holding `core_robot.md`.
        deployment: installation facts (layer 3).
        session: conversation facts (layer 4).
        user_context: reserved layer 5. V1 never populates it; the parameter exists so
            that adding per-user context later does not change this signature.

    Returns:
        The prompt text, layers separated by horizontal rules.
    """
    deployment = deployment or DeploymentContext()
    session = session or SessionContext()

    sections: list[tuple[PromptLayer, str]] = [
        (PromptLayer.CORE, _read_core_prompt(str(prompts_dir))),
        (PromptLayer.ROLE, _role_section(role)),
        (PromptLayer.DEPLOYMENT, _deployment_section(deployment)),
        (PromptLayer.SESSION, _session_section(session, role)),
    ]
    if user_context.strip():
        sections.append((PromptLayer.USER_CONTEXT, f"# About this person\n\n{user_context.strip()}"))

    # Sort defensively: the list above is already ordered, but the ordering is a
    # contract, and a future edit that appends in the wrong place should not silently
    # reorder the prompt.
    sections.sort(key=lambda item: item[0])
    return "\n\n---\n\n".join(text for _, text in sections if text.strip())


def compose_messages(
    role: RolePack,
    history: Sequence[dict[str, str]],
    user_text: str,
    *,
    prompts_dir: Path,
    deployment: DeploymentContext | None = None,
    session: SessionContext | None = None,
) -> list[dict[str, str]]:
    """Build the full chat message list: system prompt, bounded history, new user turn.

    History is passed through as chat messages rather than flattened into the system
    prompt, because every provider handles role-tagged turns better than a transcript
    embedded in a wall of instructions.
    """
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(
                role, prompts_dir=prompts_dir, deployment=deployment, session=session
            ),
        }
    ]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)
    messages.append({"role": "user", "content": user_text})
    return messages
