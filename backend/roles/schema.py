"""The Role Pack schema.

A Role Pack is the *entire* behavioural difference between a desktop companion, an
English tutor, and a museum guide. If a deployment needs a code change to alter
personality, language policy, or conversational shape, that is a schema bug, not a
feature request.

Two design rules keep this from turning into a configuration language:

- **Every field has a defensible default.** A minimal role is `id`, `identity.name` and
  `identity.role`; everything else is optional.
- **No field encodes vocabulary the core does not already understand.** `emotion_policy`
  can only narrow the built-in `Expression` set, it cannot invent expressions the face
  has no artwork for.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.emotion import Expression

#: Language codes V1 supports end to end (STT, prompts, TTS).
SupportedLanguage = Literal["fa", "en"]


class _RoleModel(BaseModel):
    """Strict base: an unknown key is a typo in someone's YAML, and typos should hurt."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Identity(_RoleModel):
    """Who the robot is, in this deployment."""

    name: str = "Fafobot"
    role: str = "social desktop companion"
    #: One or two sentences of character, injected verbatim into the prompt. Keep it
    #: short — long backstories crowd out the conversation.
    description: str = ""


class LanguagePolicy(_RoleModel):
    """How this role handles Persian and English.

    Language behaviour belongs to the role, not the core. A companion mirrors whatever
    the human speaks; a tutor understands Persian but steers toward English. Encoding
    that as role data is what keeps `if role == "teacher"` out of the pipeline.
    """

    #: Languages the role will converse in at all.
    primary: list[SupportedLanguage] = Field(default_factory=lambda: ["fa", "en"])
    #: `mirror` replies in whatever the user spoke. `prefer` replies in
    #: `preferred_language` while still understanding the others.
    policy: Literal["mirror", "prefer"] = "mirror"
    preferred_language: SupportedLanguage | None = None
    #: Extra sentence appended to the language section of the prompt, for nuance the
    #: two policies above cannot express.
    guidance: str = ""

    @model_validator(mode="after")
    def _prefer_needs_a_target(self) -> "LanguagePolicy":
        if self.policy == "prefer" and self.preferred_language is None:
            raise ValueError("languages.policy='prefer' requires 'preferred_language'")
        if self.preferred_language and self.preferred_language not in self.primary:
            raise ValueError("languages.preferred_language must appear in 'primary'")
        return self

    @field_validator("primary")
    @classmethod
    def _at_least_one(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("languages.primary must list at least one language")
        return value


class ConversationStyle(_RoleModel):
    """The shape of a spoken turn.

    Defaults are tuned for speech, not for chat: a desktop robot that answers in six
    sentences is a robot people stop talking to.
    """

    max_response_sentences: int = Field(default=3, ge=1, le=10)
    ask_followup_questions: bool = True
    avoid_monologues: bool = True
    #: Additional style notes, e.g. "use the student's name occasionally".
    notes: list[str] = Field(default_factory=list)


class Personality(_RoleModel):
    """Character dials. Coarse on purpose — three levels is enough to hear a difference,
    and a 0–100 slider would imply a precision the prompt cannot deliver."""

    warmth: Literal["low", "medium", "high"] = "medium"
    humor: Literal["none", "light", "playful"] = "light"
    curiosity: Literal["low", "medium", "high"] = "high"
    formality: Literal["casual", "neutral", "formal"] = "neutral"


class SafetyPolicy(_RoleModel):
    """Hard behavioural limits. These reinforce the core rules; they cannot relax them.

    Note what is *not* here: no field can grant the robot a capability. Turning
    `never_claim_unavailable_sensors` off does not give the robot a camera; the core
    prompt still forbids claiming one. These flags only add emphasis.
    """

    never_claim_unavailable_sensors: bool = True
    never_claim_physical_actions_not_supported: bool = True
    #: Subjects this deployment should decline and redirect, e.g. ["medical advice"].
    avoid_topics: list[str] = Field(default_factory=list)
    #: Extra deployment-specific rules, injected as prompt bullets.
    additional_rules: list[str] = Field(default_factory=list)


class EmotionPolicy(_RoleModel):
    """Which expressions this role may use, and which it rests in."""

    allowed: list[Expression] = Field(
        default_factory=lambda: [
            Expression.NEUTRAL,
            Expression.HAPPY,
            Expression.CURIOUS,
            Expression.CONFUSED,
            Expression.ENCOURAGING,
            Expression.SURPRISED,
        ]
    )
    resting: Expression = Expression.NEUTRAL

    @model_validator(mode="after")
    def _resting_must_be_allowed(self) -> "EmotionPolicy":
        if not self.allowed:
            raise ValueError("emotion_policy.allowed must not be empty")
        if self.resting not in self.allowed:
            raise ValueError("emotion_policy.resting must appear in 'allowed'")
        return self


class SessionPolicy(_RoleModel):
    """Session-boundary behaviour."""

    greeting: bool = True
    farewell: bool = True
    #: Spoken when a session opens. Empty means "let the model improvise one".
    greeting_text: str = ""


class MemoryPolicy(_RoleModel):
    """V1 supports session-scoped memory only.

    `persistent` is intentionally absent from the type. Accepting a value the system
    cannot honour would be exactly the fake capability this project refuses to ship.
    """

    mode: Literal["session_only", "none"] = "session_only"


class ToolPolicy(_RoleModel):
    """Reserved for tool calling. V1 has no tools; a non-empty list is rejected.

    This exists so Role Packs written today keep parsing tomorrow, not so that anyone
    can pretend tools work.
    """

    enabled: list[str] = Field(default_factory=list)

    @field_validator("enabled")
    @classmethod
    def _no_tools_in_v1(cls, value: list[str]) -> list[str]:
        if value:
            raise ValueError(
                "tools.enabled must be empty: tool calling is not implemented in V1"
            )
        return value


class VoiceHint(_RoleModel):
    """Optional TTS hints. Advisory — a provider that cannot honour them ignores them."""

    voice: str = ""
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class RolePack(_RoleModel):
    """A complete, validated role definition."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(default=1, ge=1)
    identity: Identity = Field(default_factory=Identity)
    #: What the role is trying to achieve, as prompt bullets.
    objective: list[str] = Field(default_factory=list)
    languages: LanguagePolicy = Field(default_factory=LanguagePolicy)
    conversation: ConversationStyle = Field(default_factory=ConversationStyle)
    personality: Personality = Field(default_factory=Personality)
    safety: SafetyPolicy = Field(default_factory=SafetyPolicy)
    emotion_policy: EmotionPolicy = Field(default_factory=EmotionPolicy)
    session: SessionPolicy = Field(default_factory=SessionPolicy)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)
    tools: ToolPolicy = Field(default_factory=ToolPolicy)
    voice: VoiceHint = Field(default_factory=VoiceHint)

    def allows(self, expression: Expression) -> bool:
        """Whether this role is permitted to show `expression`."""
        return expression in self.emotion_policy.allowed

    def coerce_expression(self, expression: Expression) -> Expression:
        """Clamp a model-chosen expression into what this role allows.

        A tutor that never looks sleepy stays awake even if the model asks for it.
        """
        return expression if self.allows(expression) else self.emotion_policy.resting
