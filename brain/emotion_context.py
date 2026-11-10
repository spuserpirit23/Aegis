"""Typed, read-only emotion context handed to response generation and the UI.

The planner builds one :class:`ResponseEmotionContext` per user turn. It
separates what AEGIS *observed* about the user from AEGIS's own *regulated*
response tendency, and keeps the observation's uncertainty attached, so
downstream code never has to reach into engine state or loose dicts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from types import MappingProxyType
from typing import Any, Mapping

from .emotion_engine import EmotionObservation, EmotionState

_AFFECTIVE_GUIDANCE = (
    "Use this only to calibrate tone. Do not claim certainty about "
    "the user's feelings or expose these internal labels."
)


def _frozen(mapping: Mapping[str, float]) -> Mapping[str, float]:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class ObservedUserEmotion:
    """What AEGIS perceives about the user.

    ``emotion``/``intensity``/``valence``/``arousal`` describe the blended
    per-user state across turns. ``turn_*``, ``confidence`` and ``source``
    describe only this turn's evidence, which can differ (one calm sentence
    from a user who has been frustrated for a while).
    """

    entity: str
    emotion: str
    intensity: float
    valence: float
    arousal: float
    turn_emotion: str
    turn_valence: float | None
    confidence: float
    source: str

    @property
    def uncertainty(self) -> float:
        return 1.0 - self.confidence


@dataclass(frozen=True)
class AegisEmotion:
    """AEGIS's regulated response tendency; never a mirror of the user."""

    tendency: str
    intensity: float
    valence: float
    arousal: float
    dominance: float
    confidence: float
    source: str
    secondary: Mapping[str, float] = field(default_factory=lambda: _frozen({}))


@dataclass(frozen=True)
class ResponseEmotionContext:
    """Everything response generation may know about emotion for one turn.

    ``label`` is the coarse legacy label after policy rules (e.g. low trust
    suppresses warmth); it drives ``mind.as_prompt``. ``user`` is ``None`` and
    ``aegis`` is a neutral baseline until the first observed turn.
    """

    label: str
    trust: str
    aegis: AegisEmotion
    user: ObservedUserEmotion | None = None

    @property
    def observed(self) -> bool:
        return self.user is not None

    @classmethod
    def baseline(cls, label: str = "neutral", trust: str = "medium") -> "ResponseEmotionContext":
        return cls(label=label, trust=trust, aegis=AegisEmotion(
            tendency=label, intensity=0.0, valence=0.0, arousal=.25,
            dominance=.5, confidence=0.0, source="baseline"))

    @classmethod
    def from_turn(cls, *, observation: EmotionObservation, user_state: EmotionState,
                  aegis_state: EmotionState, label: str, trust: str) -> "ResponseEmotionContext":
        user = ObservedUserEmotion(
            entity=observation.entity,
            emotion=user_state.primary,
            intensity=user_state.intensity,
            valence=user_state.valence,
            arousal=user_state.arousal,
            turn_emotion=observation.primary,
            turn_valence=observation.valence,
            confidence=observation.confidence,
            source=observation.source,
        )
        aegis = AegisEmotion(
            tendency=aegis_state.primary,
            intensity=aegis_state.intensity,
            valence=aegis_state.valence,
            arousal=aegis_state.arousal,
            dominance=aegis_state.dominance,
            confidence=aegis_state.confidence,
            source=aegis_state.source,
            secondary=_frozen(aegis_state.secondary),
        )
        return cls(label=label, trust=trust, aegis=aegis, user=user)

    def to_prompt(self) -> str:
        """The [AFFECTIVE CONTEXT] system-prompt block, or "" before any observation."""
        if self.user is None:
            return ""
        return (
            "[AFFECTIVE CONTEXT]\n"
            f"observed_user_emotion: {self.user.emotion}\n"
            f"observed_confidence: {self.user.confidence:.2f}\n"
            f"observed_uncertainty: {self.user.uncertainty:.2f}\n"
            f"aegis_response_tendency: {self.aegis.tendency}\n"
            f"aegis_intensity: {self.aegis.intensity:.2f}\n"
            + _AFFECTIVE_GUIDANCE
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation (mapping proxies become plain dicts)."""
        # Not asdict(): it deep-copies values and mappingproxy can't be copied.
        aegis = {f.name: getattr(self.aegis, f.name) for f in fields(self.aegis)}
        aegis["secondary"] = dict(self.aegis.secondary)
        user = None
        if self.user is not None:
            user = asdict(self.user)
            user["uncertainty"] = self.user.uncertainty
        return {"label": self.label, "trust": self.trust, "aegis": aegis, "user": user}
