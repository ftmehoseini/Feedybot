"""Role Engine tests.

The property under test throughout: **a role is data, and the core does not know which
one is loaded.** Every test here would still pass if a fourth role were added tomorrow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.emotion import Expression
from backend.errors import ConfigError
from backend.roles import list_roles, load_role
from backend.roles.schema import RolePack

from .conftest import ROLES_DIR


def test_shipped_roles_all_load() -> None:
    available = list_roles(ROLES_DIR)
    assert "social_companion" in available
    assert "english_teacher" in available
    for role_id in available:
        assert load_role(role_id, ROLES_DIR).id == role_id


def test_minimal_role_uses_defaults_for_every_optional_field() -> None:
    role = RolePack(id="minimal")
    assert role.identity.name == "Fafobot"
    assert role.languages.policy == "mirror"
    assert role.conversation.max_response_sentences == 3
    assert role.emotion_policy.resting is Expression.NEUTRAL
    assert role.memory.mode == "session_only"
    assert role.tools.enabled == []


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"id": "Bad-Id"}, "id must be lowercase with underscores"),
        ({"id": "x", "typo_field": 1}, "unknown keys are rejected, not ignored"),
        ({"id": "x", "languages": {"policy": "prefer"}}, "prefer needs a target language"),
        (
            {"id": "x", "languages": {"primary": ["en"], "policy": "prefer",
                                      "preferred_language": "fa"}},
            "preferred language must appear in primary",
        ),
        ({"id": "x", "languages": {"primary": []}}, "at least one language required"),
        (
            {"id": "x", "emotion_policy": {"allowed": ["happy"], "resting": "neutral"}},
            "resting expression must be allowed",
        ),
        ({"id": "x", "emotion_policy": {"allowed": []}}, "allowed list cannot be empty"),
        ({"id": "x", "tools": {"enabled": ["database"]}}, "V1 has no tools"),
        ({"id": "x", "memory": {"mode": "persistent"}}, "persistent memory does not exist"),
        ({"id": "x", "conversation": {"max_response_sentences": 0}}, "must say something"),
    ],
)
def test_invalid_roles_are_rejected(payload: dict, reason: str) -> None:
    with pytest.raises(Exception):
        RolePack(**payload)


def test_missing_role_names_what_is_available() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_role("does_not_exist", ROLES_DIR)
    message = str(excinfo.value)
    assert "does_not_exist" in message
    # The error must be actionable: tell the operator what they could have typed.
    assert "social_companion" in message


@pytest.mark.parametrize("role_id", ["../secrets", "/etc/passwd", "a/b", "", "Bad"])
def test_role_ids_cannot_escape_the_roles_directory(role_id: str) -> None:
    with pytest.raises(ConfigError):
        load_role(role_id, ROLES_DIR)


def test_role_file_id_must_match_its_filename(tmp_path: Path) -> None:
    (tmp_path / "alpha.yaml").write_text(yaml.safe_dump({"id": "beta"}))
    with pytest.raises(ConfigError, match="filename and id must match"):
        load_role("alpha", tmp_path)


def test_malformed_yaml_is_a_config_error(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("id: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_role("broken", tmp_path)


def test_role_clamps_expressions_it_does_not_allow(teacher_role: RolePack) -> None:
    # The teacher role declares no `sleepy`.
    assert not teacher_role.allows(Expression.SLEEPY)
    assert teacher_role.coerce_expression(Expression.SLEEPY) is Expression.ENCOURAGING
    assert teacher_role.coerce_expression(Expression.HAPPY) is Expression.HAPPY


def test_teacher_role_differs_from_companion_only_in_data(
    teacher_role: RolePack, companion_role: RolePack
) -> None:
    """The roles must differ in configuration, not in special-cased behaviour."""
    assert teacher_role.languages.policy == "prefer"
    assert teacher_role.languages.preferred_language == "en"
    assert companion_role.languages.policy == "mirror"
    assert type(teacher_role) is type(companion_role)


def test_teacher_role_refuses_to_fake_assessment(teacher_role: RolePack) -> None:
    """A tutor with no acoustic scoring must never be configured to produce scores."""
    rules = " ".join(teacher_role.safety.additional_rules).lower()
    assert "score" in rules
    assert "pronunciation" in rules


def test_no_shipped_role_enables_tools_or_persistent_memory() -> None:
    """Nothing may claim a capability V1 does not have."""
    for role_id in list_roles(ROLES_DIR):
        role = load_role(role_id, ROLES_DIR)
        assert role.tools.enabled == [], f"{role_id} enables tools that do not exist"
        assert role.memory.mode in {"session_only", "none"}


def test_core_code_contains_no_role_branching() -> None:
    """Guard against the exact anti-pattern this architecture exists to prevent.

    A regression here would break no behavioural test — it would quietly reintroduce
    `if role == "teacher"` into the pipeline — so it is asserted directly.
    """
    core_modules = [
        "backend/pipeline.py",
        "backend/communication.py",
        "backend/session.py",
        "backend/roles/prompt_builder.py",
        "backend/agent_reply.py",
    ]
    repo_root = Path(__file__).resolve().parent.parent
    for relative in core_modules:
        source = (repo_root / relative).read_text(encoding="utf-8")
        for role_id in list_roles(ROLES_DIR):
            assert role_id not in source, f"{relative} mentions the role {role_id!r}"
