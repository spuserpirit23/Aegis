"""Provider-independent, entity-aware emotional evidence and state dynamics.

This is a computational representation for downstream behavior, not a claim
about subjective experience. Providers report evidence; the engine owns state,
decay, history, and events. It never performs actions or generates replies.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Protocol

from .event_bus import EventBus


def _unit(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return min(1.0, max(0.0, value))


def _valence(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("valence must be finite")
    return min(1.0, max(-1.0, value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EmotionSpec:
    """Vocabulary metadata: valence [-1, 1], arousal/dominance [0, 1]."""

    name: str
    valence: float
    arousal: float
    dominance: float = 0.5
    half_life_seconds: float = 900.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.name.strip().lower())
        if not self.name:
            raise ValueError("emotion name cannot be empty")
        object.__setattr__(self, "valence", _valence(self.valence))
        object.__setattr__(self, "arousal", _unit(self.arousal, "arousal"))
        object.__setattr__(self, "dominance", _unit(self.dominance, "dominance"))
        if not math.isfinite(self.half_life_seconds) or self.half_life_seconds <= 0:
            raise ValueError("half_life_seconds must be a positive finite number")


_DEFAULT_SPECS = (
    EmotionSpec("happy", .82, .55, .62), EmotionSpec("joyful", .94, .72, .68),
    EmotionSpec("excited", .78, .88, .64), EmotionSpec("hopeful", .68, .54, .58),
    EmotionSpec("grateful", .78, .44, .56), EmotionSpec("loving", .88, .52, .62),
    EmotionSpec("proud", .72, .60, .76), EmotionSpec("amused", .72, .68, .60),
    EmotionSpec("sad", -.72, .32, .30, 1800), EmotionSpec("angry", -.78, .82, .70, 1200),
    EmotionSpec("frustrated", -.66, .72, .42, 1200), EmotionSpec("afraid", -.82, .82, .24, 900),
    EmotionSpec("anxious", -.68, .72, .28, 1500), EmotionSpec("guilty", -.62, .48, .32, 1800),
    EmotionSpec("lonely", -.64, .28, .28, 2400), EmotionSpec("disappointed", -.64, .42, .36, 1800),
    EmotionSpec("calm", .18, .18, .56, 1800), EmotionSpec("curious", .28, .58, .48),
    EmotionSpec("confused", -.12, .48, .38), EmotionSpec("surprised", .08, .88, .50, 30),
    EmotionSpec("cautious", -.12, .42, .48), EmotionSpec("concerned", -.36, .48, .48, 1500),
    EmotionSpec("relaxed", .48, .18, .54, 1800), EmotionSpec("focused", .12, .38, .62, 1200),
    EmotionSpec("neutral", 0.0, .25, .5, 900),
)


class EmotionVocabulary:
    """Extensible emotion metadata registry."""

    def __init__(self, specs=()) -> None:
        self._specs = {spec.name: spec for spec in _DEFAULT_SPECS}
        for spec in specs:
            self.register(spec)

    def register(self, spec: EmotionSpec) -> None:
        if not isinstance(spec, EmotionSpec):
            raise TypeError("spec must be an EmotionSpec")
        self._specs[spec.name] = spec

    def get(self, name: str) -> EmotionSpec:
        try:
            return self._specs[name.strip().lower()]
        except KeyError as exc:
            raise ValueError(f"unknown emotion {name!r}; register its EmotionSpec first") from exc

    def __contains__(self, name: str) -> bool:
        return name.strip().lower() in self._specs

    def __iter__(self):
        return iter(self._specs.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)


@dataclass
class EmotionObservation:
    """Emotion evidence about one entity; it is not itself persistent state."""

    entity: str
    emotions: dict[str, float]
    valence: float | None = None
    arousal: float | None = None
    confidence: float = 1.0
    source: str = "unknown"
    timestamp: str = field(default_factory=_utc_now)
    dominance: float | None = None
    context: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.entity = self.entity.strip() or "unknown"
        cleaned = {}
        for name, score in self.emotions.items():
            score = _unit(score, name)
            if score:
                cleaned[name.strip().lower()] = score
        self.emotions = cleaned
        if self.valence is not None:
            self.valence = _valence(self.valence)
        if self.arousal is not None:
            self.arousal = _unit(self.arousal, "arousal")
        if self.dominance is not None:
            self.dominance = _unit(self.dominance, "dominance")
        self.confidence = _unit(self.confidence, "confidence")
        self.context = dict(self.context or {})

    @property
    def primary(self) -> str:
        return max(self.emotions, key=self.emotions.get) if self.emotions else "neutral"

    @property
    def intensity(self) -> float:
        return max(self.emotions.values(), default=0.0)

    @property
    def uncertainty(self) -> float:
        """How much downstream policy should discount this observation."""
        return 1.0 - self.confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity, "primary": self.primary,
            "emotions": dict(self.emotions), "intensity": self.intensity,
            "valence": self.valence, "arousal": self.arousal,
            "dominance": self.dominance, "confidence": self.confidence,
            "uncertainty": self.uncertainty, "source": self.source,
            "timestamp": self.timestamp, "context": dict(self.context),
        }


@dataclass
class EmotionState:
    """Current multidimensional emotional representation for one entity."""

    entity: str
    emotions: dict[str, float] = field(default_factory=dict)
    valence: float = 0.0
    arousal: float = .25
    dominance: float = .5
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)
    updated_at: str = field(default_factory=_utc_now)
    source: str = "baseline"

    def __post_init__(self) -> None:
        self.emotions = {name: _unit(score, name) for name, score in self.emotions.items()}
        self.valence = _valence(self.valence)
        self.arousal = _unit(self.arousal, "arousal")
        self.dominance = _unit(self.dominance, "dominance")
        self.confidence = _unit(self.confidence, "confidence")

    @property
    def primary(self) -> str:
        return max(self.emotions, key=self.emotions.get) if self.emotions else "neutral"

    @property
    def secondary(self) -> dict[str, float]:
        return {name: score for name, score in self.emotions.items() if name != self.primary}

    @property
    def intensity(self) -> float:
        return max(self.emotions.values(), default=0.0)

    def to_dict(self) -> dict:
        return {"entity": self.entity, "primary": self.primary,
                "emotions": dict(self.emotions), "secondary": self.secondary,
                "intensity": self.intensity, "valence": self.valence,
                "arousal": self.arousal, "dominance": self.dominance,
                "confidence": self.confidence, "timestamp": self.updated_at,
                "source": self.source}

    def copy(self) -> "EmotionState":
        return EmotionState(self.entity, dict(self.emotions), self.valence, self.arousal,
                            self.dominance, self.confidence, self.timestamp,
                            self.updated_at, self.source)


class EmotionProvider(Protocol):
    """Provider contract. Implementations return evidence and never mutate state."""
    name: str

    def analyze(self, text: str, *, entity: str = "user", context: Mapping | None = None) -> EmotionObservation:
        ...


_RULES: dict[str, tuple[tuple[str, float], ...]] = {
    "frustrated": (("frustrated", .85), ("nothing works", .82), ("doesn't work", .70), ("does not work", .70), ("not working", .72), ("stuck", .62), ("annoying", .55)),
    "angry": (("furious", .92), ("pissed off", .88), ("angry", .82), ("mad at", .68), ("hate", .58)),
    "anxious": (("worried", .72), ("anxious", .84), ("nervous", .68), ("what will happen", .60)),
    "afraid": (("scared", .82), ("afraid", .82), ("terrified", .93), ("i fear", .68)),
    "sad": (("sad", .82), ("upset", .58), ("heartbroken", .94), ("miserable", .88)),
    "disappointed": (("disappointed", .78), ("let down", .68)),
    "lonely": (("lonely", .86), ("alone", .45)),
    "guilty": (("guilty", .82), ("my fault", .64), ("i regret", .60)),
    "happy": (("happy", .72), ("great", .55), ("awesome", .76), ("nice", .48), ("cool", .45)),
    "joyful": (("overjoyed", .92), ("so happy", .84), ("delighted", .82)),
    "excited": (("excited", .82), ("thrilled", .86), ("can't wait", .70), ("cannot wait", .70)),
    "hopeful": (("hopeful", .72), ("looking forward", .62)),
    "grateful": (("thank you", .64), ("thanks", .55), ("grateful", .82)),
    "loving": (("love you", .82), ("i love", .72)),
    "proud": (("proud", .76), ("nailed it", .62)),
    "amused": (("haha", .58), ("that's funny", .66), ("that is funny", .66)),
    "curious": (("curious", .72), ("wonder", .40), ("how does", .42), ("why does", .40)),
    "confused": (("confused", .78), ("not sure", .50), ("don't understand", .66), ("do not understand", .66)),
    "surprised": (("surprised", .76), ("wow", .56), ("unexpected", .64)),
    "cautious": (("careful", .42), ("cautious", .68), ("risky", .54)),
    "concerned": (("concerned", .72), ("something is wrong", .60)),
    "calm": (("calm", .72), ("it's okay", .48), ("it is okay", .48)),
    "relaxed": (("relaxed", .72), ("at ease", .64)),
    "focused": (("focused", .70), ("concentrating", .62)),
}
_NEGATOR = re.compile(r"\b(?:not|never|no longer|isn't|aren't|wasn't|weren't|don't|doesn't|didn't|cannot|can't)\b(?:\W+\w+){0,2}\W*$", re.I)


class RuleBasedEmotionProvider:
    """Small rule provider, dependency-free and intentionally conservative."""
    name = "rule"

    def analyze(self, text: str, *, entity: str = "user", context: Mapping | None = None) -> EmotionObservation:
        clean = " ".join((text or "").lower().split())
        hits: dict[str, float] = {}
        for emotion, phrases in _RULES.items():
            for phrase, strength in phrases:
                match = re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", clean)
                if not match:
                    continue
                # Reverse common negation ("not angry", "don't feel sad").
                if _NEGATOR.search(clean[max(0, match.start() - 35):match.start()]):
                    continue
                hits[emotion] = max(hits.get(emotion, 0), strength)
        if not hits:
            return EmotionObservation(entity, {}, valence=0, arousal=.25, confidence=.55, source=self.name)
        negators = sum(bool(re.search(r"\b" + x + r"\b", clean)) for x in ("not", "never", "don't", "doesn't", "didn't", "can't"))
        if negators:
            hits = {name: min(1.0, score + .08) for name, score in hits.items()}
        specs = {spec.name: spec for spec in _DEFAULT_SPECS}
        total = sum(hits.values())
        valence = sum(specs[name].valence * score for name, score in hits.items()) / total
        arousal = sum(specs[name].arousal * score for name, score in hits.items()) / total
        confidence = min(.9, .55 + .08 * len(hits) + .12 * max(hits.values()))
        return EmotionObservation(entity, hits, valence, arousal, confidence, self.name)


class SemanticEmotionProvider:
    """Adapter around an injected semantic analyzer; no vendor dependency."""
    name = "semantic"

    def __init__(self, analyzer: Callable, *, name: str = "semantic") -> None:
        self.analyzer = analyzer
        self.name = name

    def analyze(self, text: str, *, entity: str = "user", context: Mapping | None = None) -> EmotionObservation:
        result = self.analyzer(text=text, entity=entity, context=context or {})
        if isinstance(result, EmotionObservation):
            if result.entity != entity:
                result.entity = entity
            return result
        if isinstance(result, str):
            # Model adapters commonly return a JSON object as text.  Keeping
            # parsing here makes local and cloud adapters behave identically.
            try:
                result = json.loads(result)
            except json.JSONDecodeError as exc:
                raise ValueError("semantic emotion provider returned invalid JSON") from exc
        if not isinstance(result, Mapping):
            raise TypeError("semantic emotion provider must return an EmotionObservation, mapping, or JSON object")
        payload = dict(result)
        payload.pop("entity", None)
        payload.setdefault("emotions", {})
        payload.setdefault("source", self.name)
        # Some model prompts use uncertainty instead of confidence.  Accept
        # either, but expose one canonical confidence field internally.
        if "confidence" not in payload and "uncertainty" in payload:
            payload["confidence"] = 1.0 - float(payload.pop("uncertainty"))
        payload.pop("uncertainty", None)
        return EmotionObservation(entity=entity, **payload)


class LocalSemanticEmotionProvider(SemanticEmotionProvider):
    """Named semantic adapter for an injected on-device model callable."""

    def __init__(self, analyzer: Callable) -> None:
        super().__init__(analyzer, name="local")


class CloudSemanticEmotionProvider(SemanticEmotionProvider):
    """Named semantic adapter for an injected cloud model callable.

    Network/authentication concerns deliberately remain in the caller's
    adapter.  The emotion core therefore has no vendor credentials or SDK
    dependency and can use the same structured contract locally or remotely.
    """

    def __init__(self, analyzer: Callable) -> None:
        super().__init__(analyzer, name="cloud")


class FallbackEmotionProvider:
    """Try normalized providers in order, then return rule-based evidence.

    This is useful when selection belongs outside ``EmotionEngine`` (for
    example a deployment that prefers an on-device model before a cloud one).
    """

    name = "fallback"

    def __init__(self, providers: Iterable[EmotionProvider], *, fallback: EmotionProvider | None = None) -> None:
        self.providers = list(providers)
        self.fallback = fallback or RuleBasedEmotionProvider()
        if not self.providers:
            raise ValueError("fallback provider needs at least one primary provider")
        # (provider name, exception) pairs from the most recent analyze() call.
        # EmotionEngine reads this to emit one emotion.provider_fallback each.
        self.last_failures: list[tuple[str, Exception]] = []

    def analyze(self, text: str, *, entity: str = "user", context: Mapping | None = None) -> EmotionObservation:
        self.last_failures = []
        for provider in self.providers:
            try:
                return provider.analyze(text, entity=entity, context=context)
            except Exception as exc:
                self.last_failures.append((getattr(provider, "name", type(provider).__name__), exc))
                continue
        observation = self.fallback.analyze(text, entity=entity, context=context)
        observation.source = f"{getattr(self.fallback, 'name', 'rule')}:fallback"
        return observation


class EmotionalRegulation:
    """Maps another entity's emotion into AEGIS's own response tendency.

    This is deliberately *not* mirroring: observed anger never becomes AEGIS
    anger. It is a configurable policy inspired by the regulation hypothesis in
    the source research, and should stay a hypothesis rather than a fact.
    """

    NEGATIVE = {"sad", "angry", "frustrated", "afraid", "anxious", "guilty",
                "lonely", "disappointed", "concerned", "confused"}
    POSITIVE = {"happy", "joyful", "excited", "hopeful", "grateful", "loving",
                "proud", "amused", "relaxed", "calm"}

    def __init__(self, concern_gain: float = .45, concern_base: float = .30,
                 calm_base: float = .54, calm_under_stress: float = .54) -> None:
        self.concern_gain = _unit(concern_gain, "concern_gain")
        self.concern_base = _unit(concern_base, "concern_base")
        self.calm_base = _unit(calm_base, "calm_base")
        self.calm_under_stress = _unit(calm_under_stress, "calm_under_stress")

    def regulate(self, observation: EmotionObservation, *, entity: str = "aegis") -> EmotionObservation:
        intensity = observation.intensity
        negative_mass = sum(score for name, score in observation.emotions.items() if name in self.NEGATIVE)
        positive_mass = sum(score for name, score in observation.emotions.items() if name in self.POSITIVE)
        valence = observation.valence if observation.valence is not None else 0.0
        arousal = observation.arousal if observation.arousal is not None else .25
        emotions: dict[str, float] = {}

        if negative_mass > positive_mass or valence < -.15:
            # Stay steady and concerned rather than escalating with the speaker.
            emotions["concerned"] = _unit(self.concern_base + self.concern_gain * max(negative_mass, intensity), "concerned")
            emotions["calm"] = _unit(self.calm_under_stress, "calm")
            emotions["focused"] = .30
            target_valence = -.20 * intensity
            # Inverse-arousal tendency: lower response arousal under high stress.
            target_arousal = _unit(.55 - .45 * arousal + .15, "arousal")
            dominance = .58
        elif positive_mass > 0:
            emotions["happy"] = _unit(.25 + .40 * positive_mass, "happy")
            emotions["calm"] = self.calm_base
            emotions["amused"] = _unit(.20 * positive_mass, "amused")
            target_valence = .30 * positive_mass
            target_arousal = _unit(.35 + .25 * arousal, "arousal")
            dominance = .55
        else:
            emotions["calm"] = self.calm_base
            emotions["focused"] = .22
            target_valence = .05
            target_arousal = .30
            dominance = .52

        # Policy output is deterministic, so it carries full policy confidence
        # rather than the perception provider's uncertainty.
        return EmotionObservation(entity, emotions, valence=target_valence,
                                  arousal=target_arousal, dominance=dominance,
                                  confidence=1.0, source="regulation")


_LEGACY_LABELS = {"happy": "happy", "joyful": "happy", "excited": "happy",
                  "hopeful": "happy", "grateful": "happy", "loving": "happy",
                  "proud": "happy", "amused": "happy", "relaxed": "happy",
                  "concerned": "concerned", "sad": "concerned", "angry": "concerned",
                  "frustrated": "concerned", "afraid": "concerned", "anxious": "concerned",
                  "guilty": "concerned", "lonely": "concerned", "disappointed": "concerned",
                  "confused": "thinking", "curious": "thinking", "cautious": "thinking",
                  "neutral": "neutral", "calm": "neutral", "focused": "neutral",
                  "surprised": "neutral"}


def legacy_label(state: EmotionState) -> str:
    """Map rich state to the four labels the existing Mind prompt accepts."""
    return _LEGACY_LABELS.get(state.primary, "neutral")


EMOTION_EVENTS: dict[str, dict[str, str]] = {
    # Contract for every event EmotionEngine emits. Each payload contains
    # exactly these keys (plus "event" when read from events() or an EventBus).
    # Values typed EmotionState/EmotionObservation are snapshots: mutating
    # them never affects engine state. tests/test_emotion_events.py enforces
    # that this table and the emitted payloads stay in sync.
    "emotion.detected": {
        "entity": "str - who the evidence is about, e.g. 'user:sam' or 'aegis'",
        "observation": "EmotionObservation - normalized provider evidence",
        "source": "str - provider that produced it: rule, local, cloud, rule:fallback, ...",
        "timestamp": "float - engine clock time of emission",
    },
    "emotion.changed": {
        "entity": "str",
        "previous": "str - primary emotion before (neutral when there was none)",
        "current": "str - primary emotion after",
        "intensity": "float [0, 1] - strength of the new primary emotion",
        "cause": "str - 'observation' (new evidence) or 'decay' (time passing)",
        "state": "EmotionState - full state after the change",
        "timestamp": "float",
    },
    "emotion.state_changed": {
        "entity": "str",
        "state": "EmotionState - full state after every update, even if primary is unchanged",
        "timestamp": "float",
    },
    "emotion.decayed": {
        "entity": "str",
        "state": "EmotionState - state after decay. Emitted lazily when state() is read, not on a timer",
        "timestamp": "float",
    },
    "emotion.provider_fallback": {
        "entity": "str",
        "provider": "str - provider that failed",
        "fallback": "str - provider that supplied the evidence instead",
        "error": "str - exception type and message, truncated to 200 characters",
        "timestamp": "float",
    },
}


def _describe_error(exc: Exception) -> str:
    message = str(exc).strip()
    text = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return text[:200]


class EmotionEngine:
    """Owns per-entity emotion state, temporal decay, history, and change events.

    Event payloads are documented in :data:`EMOTION_EVENTS`.
    """

    def __init__(self, provider: EmotionProvider | None = None, *,
                 providers: Mapping[str, EmotionProvider] | None = None,
                 provider_name: str = "rule", persistence: float = .72,
                 decay: bool = True, history_limit: int = 100,
                 change_threshold: float = .03, regulation: EmotionalRegulation | None = None,
                 clock: Callable[[], float] = time.time, event_bus: EventBus | None = None,
                 event_limit: int = 500, dimension_half_life_seconds: float = 900.0) -> None:
        self.vocabulary = EmotionVocabulary()
        self.provider = provider or (providers or {}).get(provider_name) or RuleBasedEmotionProvider()
        self.providers = dict(providers or {})
        self.providers.setdefault(getattr(self.provider, "name", provider_name), self.provider)
        self.provider_name = getattr(self.provider, "name", provider_name)
        self.persistence = _unit(persistence, "persistence")
        self.decay_enabled = bool(decay)
        self.history_limit = max(1, int(history_limit))
        # Accepted for backward compatibility; not currently used by update().
        self.change_threshold = _unit(change_threshold, "change_threshold")
        if not math.isfinite(dimension_half_life_seconds) or dimension_half_life_seconds <= 0:
            raise ValueError("dimension_half_life_seconds must be a positive finite number")
        self.dimension_half_life_seconds = float(dimension_half_life_seconds)
        self.regulator = regulation or EmotionalRegulation()
        self._clock = clock
        self._states: dict[str, EmotionState] = {}
        self._history: dict[str, list[EmotionState]] = {}
        self._subscribers: dict[str, list[Callable[[dict], None]]] = {}
        self._events: list[dict] = []
        self.event_limit = max(1, int(event_limit))
        self.event_bus = event_bus
        # Set by brain.emotion_providers.build_emotion_engine for diagnostics.
        self.config = None

    def register_emotion(self, spec: EmotionSpec) -> None:
        self.vocabulary.register(spec)

    def register_provider(self, name: str, provider: EmotionProvider) -> None:
        if not name.strip():
            raise ValueError("provider name cannot be empty")
        self.providers[name] = provider

    def use_provider(self, name: str) -> None:
        if name not in self.providers:
            raise ValueError(f"unknown emotion provider {name!r}")
        self.provider_name, self.provider = name, self.providers[name]

    def on(self, event: str, callback: Callable[[dict], None]) -> None:
        self._subscribers.setdefault(event, []).append(callback)

    def _emit(self, event: str, payload: dict) -> None:
        payload = {**payload, "timestamp": self._clock()}
        self._events.append({"event": event, **payload})
        if len(self._events) > self.event_limit:
            del self._events[:-self.event_limit]
        if self.event_bus is not None:
            self.event_bus.publish(event, payload)
        for callback in self._subscribers.get(event, []) + self._subscribers.get("*", []):
            try:
                callback(dict(payload))
            except Exception:
                # Consumer callbacks must not corrupt emotion state updates.
                continue

    def events(self, *, clear: bool = False) -> list[dict]:
        result = list(self._events)
        if clear:
            self._events.clear()
        return result

    def process(self, text: str, *, entity: str = "user", context: Mapping | None = None,
                provider: str | None = None) -> EmotionObservation:
        selected = self.providers.get(provider) if provider else self.provider
        if selected is None:
            raise ValueError(f"unknown emotion provider {provider!r}")
        try:
            observation = selected.analyze(text, entity=entity, context=context)
        except Exception as exc:
            if selected is self.provider and isinstance(selected, RuleBasedEmotionProvider):
                raise
            observation = RuleBasedEmotionProvider().analyze(text, entity=entity, context=context)
            self._emit("emotion.provider_fallback", {
                "entity": entity, "provider": getattr(selected, "name", provider),
                "fallback": "rule", "error": _describe_error(exc)})
        else:
            # Chained providers recover internally; surface what they skipped.
            for failed, exc in getattr(selected, "last_failures", None) or ():
                self._emit("emotion.provider_fallback", {
                    "entity": entity, "provider": failed,
                    "fallback": observation.source, "error": _describe_error(exc)})
        if observation.entity != entity:
            observation.entity = entity
        # Preserve interaction context alongside evidence so semantic
        # providers and diagnostics can explain how a state was reached.
        observation.context = dict(context or observation.context)
        for name in observation.emotions:
            if name not in self.vocabulary:
                # Providers may introduce custom labels without breaking state processing.
                self.register_emotion(EmotionSpec(name, observation.valence or 0, observation.arousal or .25))
        self._emit("emotion.detected", {"entity": entity, "source": observation.source,
                                        "observation": replace(observation, emotions=dict(observation.emotions))})
        self.update(observation)
        return observation

    def update(self, observation: EmotionObservation) -> EmotionState:
        if not isinstance(observation, EmotionObservation):
            raise TypeError("observation must be an EmotionObservation")
        entity = observation.entity
        now = self._clock()
        old = self._states.get(entity)
        old = self._decayed_copy(old, now) if old else EmotionState(entity=entity, timestamp=now)
        before = old.primary
        first_state = not old.emotions
        scores = dict(old.emotions)
        blend = 1 - self.persistence
        for name, evidence in observation.emotions.items():
            incoming = evidence * observation.confidence
            prior = scores.get(name)
            if prior is None:
                # First sighting establishes the signal instead of fading it
                # toward zero from an empty baseline.
                score = incoming
            else:
                # Sustained evidence must not decay away while the stimulus
                # continues, so a strong current reading is a floor.
                score = max(self.persistence * prior + blend * incoming, .75 * incoming)
            scores[name] = _unit(score, name)
        scores = {name: score for name, score in scores.items() if score >= .005}
        # Dimensions blend independently and are derived from vocab metadata if omitted.
        if observation.valence is None:
            denominator = sum(observation.emotions.values())
            incoming_valence = (sum(self.vocabulary.get(n).valence * s for n, s in observation.emotions.items()) / denominator) if denominator else old.valence
        else:
            incoming_valence = observation.valence
        if observation.arousal is None:
            denominator = sum(observation.emotions.values())
            incoming_arousal = (sum(self.vocabulary.get(n).arousal * s for n, s in observation.emotions.items()) / denominator) if denominator else old.arousal
        else:
            incoming_arousal = observation.arousal
        if observation.dominance is None:
            denominator = sum(observation.emotions.values())
            incoming_dominance = (sum(self.vocabulary.get(n).dominance * s for n, s in observation.emotions.items()) / denominator) if denominator else old.dominance
        else:
            incoming_dominance = observation.dominance
        if first_state:
            valence = _valence(incoming_valence)
            arousal = _unit(incoming_arousal, "arousal")
            dominance = _unit(incoming_dominance, "dominance")
        else:
            # Dimensions persist and drift toward the new evidence more slowly
            # than discrete labels, matching the state-vs-perception split.
            valence = _valence(old.valence * self.persistence + incoming_valence * blend)
            arousal = _unit(old.arousal * self.persistence + incoming_arousal * blend, "arousal")
            dominance = _unit(old.dominance * self.persistence + incoming_dominance * blend, "dominance")
        state = EmotionState(entity, scores, valence, arousal, dominance,
                             observation.confidence, now, _utc_now(), observation.source)
        self._states[entity] = state
        self._history.setdefault(entity, []).append(state.copy())
        self._history[entity] = self._history[entity][-self.history_limit:]
        if before != state.primary:
            self._emit("emotion.changed", {"entity": entity, "previous": before,
                                           "current": state.primary, "intensity": state.intensity,
                                           "cause": "observation", "state": state.copy()})
        self._emit("emotion.state_changed", {"entity": entity, "state": state.copy()})
        return state.copy()

    def _decayed_copy(self, state: EmotionState | None, now: float) -> EmotionState:
        if state is None:
            return EmotionState("unknown", timestamp=now)
        result = state.copy()
        elapsed = max(0.0, now - state.timestamp)
        if not self.decay_enabled or elapsed <= 0:
            result.timestamp = now
            return result
        result.emotions = {
            name: score * math.pow(.5, elapsed / self.vocabulary.get(name).half_life_seconds)
            for name, score in result.emotions.items()
            if score * math.pow(.5, elapsed / self.vocabulary.get(name).half_life_seconds) >= .005
        }
        # Emotional dimensions drift toward neutral baseline under silence.
        dimension_factor = math.pow(.5, elapsed / self.dimension_half_life_seconds)
        result.valence *= dimension_factor
        result.arousal = .25 + (result.arousal - .25) * dimension_factor
        result.confidence *= dimension_factor
        result.timestamp = now
        result.updated_at = _utc_now()
        return result

    def state(self, entity: str = "aegis") -> EmotionState:
        now = self._clock()
        current = self._states.get(entity)
        if current is None:
            return EmotionState(entity, timestamp=now)
        decayed = self._decayed_copy(current, now)
        if decayed.emotions != current.emotions or abs(decayed.valence - current.valence) > 1e-9:
            self._states[entity] = decayed
            self._history.setdefault(entity, []).append(decayed.copy())
            self._history[entity] = self._history[entity][-self.history_limit:]
            self._emit("emotion.decayed", {"entity": entity, "state": decayed.copy()})
            if decayed.primary != current.primary:
                self._emit("emotion.changed", {"entity": entity, "previous": current.primary,
                                               "current": decayed.primary, "intensity": decayed.intensity,
                                               "cause": "decay", "state": decayed.copy()})
        return decayed.copy()

    def get_entity(self, entity: str) -> EmotionState:
        return self.state(entity)

    def history(self, entity: str | None = None) -> list[EmotionState]:
        if entity is not None:
            return [s.copy() for s in self._history.get(entity, [])]
        return [s.copy() for states in self._history.values() for s in states]

    def emotion_intensity(self, entity: str = "aegis") -> float:
        return self.state(entity).intensity

    def emotion_valence(self, entity: str = "aegis") -> float:
        return self.state(entity).valence

    def emotion_arousal(self, entity: str = "aegis") -> float:
        return self.state(entity).arousal

    def regulate(self, observation: EmotionObservation, *, entity: str = "aegis") -> EmotionState:
        """Update AEGIS's own state from another entity's observed emotion."""
        return self.update(self.regulator.regulate(observation, entity=entity))


def main(argv: list[str] | None = None) -> int:
    """Run the dependency-free emotion engine from a terminal.

    This intentionally uses only :class:`RuleBasedEmotionProvider`, making it
    useful as a smoke test and as an example integration for other providers.
    """
    parser = argparse.ArgumentParser(
        description="Analyze emotion evidence and update AEGIS emotion state.",
    )
    parser.add_argument(
        "text",
        nargs="?",
        default="I've been trying this for hours and nothing works!",
        help="Text to analyze (defaults to a short demo).",
    )
    parser.add_argument("--entity", default="user", help="Entity that produced the text.")
    parser.add_argument(
        "--regulate",
        action="store_true",
        help="Also show AEGIS's regulated, non-mirroring state.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    engine = EmotionEngine()
    observation = engine.process(args.text, entity=args.entity)
    payload: dict[str, object] = {
        "observation": {
            "entity": observation.entity,
            "primary": observation.primary,
            "emotions": observation.emotions,
            "intensity": observation.intensity,
            "valence": observation.valence,
            "arousal": observation.arousal,
            "dominance": observation.dominance,
            "confidence": observation.confidence,
            "source": observation.source,
        },
        "state": engine.state(args.entity).to_dict(),
        "events": [event["event"] for event in engine.events()],
    }
    if args.regulate:
        payload["aegis_state"] = engine.regulate(observation).to_dict()

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Observed {payload['observation']['primary']} in {args.entity}.")
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
