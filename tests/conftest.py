"""Shared fixtures.

Every fixture here is offline by construction: no hardware, no API keys, no network.
A test that needs any of those is a test that gets skipped in CI and therefore a test
that does not exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import Settings
from backend.emotion import Expression, SystemState
from backend.pipeline import ConversationPipeline
from backend.providers.llm.fake import FakeLLMProvider
from backend.providers.registry import ProviderBundle
from backend.providers.stt.fake import FakeSTTProvider
from backend.providers.tts.fake import FakeTTSProvider
from backend.roles import load_role
from backend.roles.schema import RolePack
from backend.session import RobotSession

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLES_DIR = REPO_ROOT / "roles"
PROMPTS_DIR = REPO_ROOT / "backend" / "prompts"


@pytest.fixture
def settings() -> Settings:
    """Default settings with the fake providers and short timeouts."""
    return Settings(
        roles_dir=ROLES_DIR,
        prompts_dir=PROMPTS_DIR,
        stt_timeout_s=2.0,
        llm_timeout_s=2.0,
        tts_timeout_s=2.0,
        max_history_turns=4,
    )


@pytest.fixture
def companion_role() -> RolePack:
    return load_role("social_companion", ROLES_DIR)


@pytest.fixture
def teacher_role() -> RolePack:
    return load_role("english_teacher", ROLES_DIR)


@pytest.fixture
def session(companion_role: RolePack) -> RobotSession:
    robot_session = RobotSession(role=companion_role, device_id="test-device")
    robot_session.force_state(SystemState.LISTENING)
    return robot_session


class RecordingSink:
    """A `TurnSink` that records everything instead of sending it.

    This is what makes the pipeline testable without a socket: the tests assert on the
    exact sequence of effects a real robot would have received.
    """

    def __init__(self) -> None:
        self.states: list[SystemState] = []
        self.expressions: list[tuple[Expression, int]] = []
        self.audio = bytearray()
        self.speak_starts: list[int] = []
        self.speak_ends: list[int] = []
        self.errors: list[tuple[str, str]] = []
        #: Every effect in order, for asserting on sequencing rather than just contents.
        self.timeline: list[str] = []

    async def set_state(self, state: SystemState) -> None:
        self.states.append(state)
        self.timeline.append(f"state:{state.value}")

    async def set_expression(self, expression: Expression, hold_ms: int) -> None:
        self.expressions.append((expression, hold_ms))
        self.timeline.append(f"expression:{expression.value}")

    async def speak_start(self, turn_id: int, sample_rate: int) -> None:
        self.speak_starts.append(turn_id)
        self.timeline.append("speak_start")

    async def speak_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)
        self.timeline.append("audio")

    async def speak_end(self, turn_id: int) -> None:
        self.speak_ends.append(turn_id)
        self.timeline.append("speak_end")

    async def report_error(self, code: str, message: str) -> None:
        self.errors.append((code, message))
        self.timeline.append(f"error:{code}")


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def providers() -> ProviderBundle:
    return ProviderBundle(stt=FakeSTTProvider(), llm=FakeLLMProvider(), tts=FakeTTSProvider())


@pytest.fixture
def pipeline(providers: ProviderBundle, settings: Settings) -> ConversationPipeline:
    return ConversationPipeline(providers, settings)


def make_pipeline(settings: Settings, *, stt=None, llm=None, tts=None) -> ConversationPipeline:
    """Build a pipeline with selected providers replaced. Used by the failure tests."""
    bundle = ProviderBundle(
        stt=stt or FakeSTTProvider(),
        llm=llm or FakeLLMProvider(),
        tts=tts or FakeTTSProvider(),
    )
    return ConversationPipeline(bundle, settings)
