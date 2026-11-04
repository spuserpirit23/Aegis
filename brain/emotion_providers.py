"""Configuration-driven emotion provider selection.

``brain.emotion_engine`` stays vendor-free; this module is where concrete
analyzers (a local transformers classifier, a Gemini client) are adapted to the
semantic provider contract and chosen from configuration.

Selection comes from ``provider`` in ``mind/emotion.yaml``, overridden by
``AEGIS_EMOTION_PROVIDER`` (see ``brain.emotion_config``):

``rule``  (default) dependency-free keyword rules, no model calls.
``local`` on-device transformers classifier, falls back to rules.
``cloud`` Gemini structured analysis, falls back to rules. Sends user text to
          the cloud provider and adds one model call per turn.
``auto``  local, then cloud, then rules.

Selection never raises: an unknown name or a missing dependency logs a warning
and degrades to the rule provider so the assistant can always start.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from typing import Any, Callable, Mapping

from .emotion_engine import (
    CloudSemanticEmotionProvider,
    EmotionalRegulation,
    EmotionEngine,
    EmotionObservation,
    EmotionProvider,
    FallbackEmotionProvider,
    LocalSemanticEmotionProvider,
    RuleBasedEmotionProvider,
    _DEFAULT_SPECS,
)
from .emotion_config import EmotionConfig, load_emotion_config
from .event_bus import EventBus

log = logging.getLogger(__name__)

PROVIDER_ENV = "AEGIS_EMOTION_PROVIDER"
CLOUD_MODEL_ENV = "AEGIS_EMOTION_CLOUD_MODEL"
LOCAL_MODEL_ENV = "AEGIS_EMOTION_LOCAL_MODEL"
DEFAULT_CLOUD_MODEL = "gemini-3.6-flash"
DEFAULT_LOCAL_MODEL = "j-hartmann/emotion-english-distilroberta-base"
PROVIDER_CHOICES = ("rule", "local", "cloud", "auto")

_SPECS = {spec.name: spec for spec in _DEFAULT_SPECS}

# j-hartmann/emotion-english-distilroberta-base labels -> AEGIS vocabulary.
# "disgust" has no direct equivalent; it is closest to anger in valence and
# arousal, so it reinforces "angry" rather than introducing a new label.
_LOCAL_LABELS = {
    "anger": "angry",
    "disgust": "angry",
    "fear": "afraid",
    "joy": "happy",
    "sadness": "sad",
    "surprise": "surprised",
    "neutral": None,
}

_OBSERVATION_KEYS = ("emotions", "valence", "arousal", "dominance", "confidence", "uncertainty")


def _observation_from_scores(entity: str, emotions: dict[str, float], confidence: float,
                             source: str) -> EmotionObservation:
    """Derive valence/arousal from vocabulary specs, as the rule provider does."""
    known = {name: score for name, score in emotions.items() if name in _SPECS and score > 0}
    total = sum(known.values())
    if not total:
        return EmotionObservation(entity, {}, valence=0, arousal=.25, confidence=confidence, source=source)
    valence = sum(_SPECS[n].valence * s for n, s in known.items()) / total
    arousal = sum(_SPECS[n].arousal * s for n, s in known.items()) / total
    return EmotionObservation(entity, emotions, valence, arousal, confidence, source)


class LocalClassifierAnalyzer:
    """Semantic analyzer backed by a lazily loaded transformers pipeline."""

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL, *, pipeline_factory: Callable | None = None,
                 min_score: float = .05) -> None:
        self.model = model
        self.min_score = min_score
        self._factory = pipeline_factory
        self._pipeline = None

    def _classifier(self):
        if self._pipeline is None:
            factory = self._factory
            if factory is None:
                from transformers import pipeline as factory  # optional dependency
            self._pipeline = factory("text-classification", model=self.model, top_k=None)
        return self._pipeline

    def __call__(self, *, text: str, entity: str, context: Mapping) -> EmotionObservation:
        raw = self._classifier()(text)
        # Pipelines return [[{label, score}, ...]] for a single string with top_k=None.
        scores = raw[0] if raw and isinstance(raw[0], list) else raw
        emotions: dict[str, float] = {}
        neutral = 0.0
        for item in scores:
            label, score = str(item["label"]).lower(), float(item["score"])
            mapped = _LOCAL_LABELS.get(label, label)
            if mapped is None:
                neutral = max(neutral, score)
            elif score >= self.min_score:
                emotions[mapped] = max(emotions.get(mapped, 0.0), score)
        top = max([neutral, *emotions.values()], default=0.0)
        return _observation_from_scores(entity, emotions, confidence=top, source="local")


_CLOUD_INSTRUCTION = (
    "You estimate the emotional state expressed by the speaker of a message. "
    "Reply with one JSON object only, with keys: "
    '"emotions" (object mapping emotion name to intensity 0..1, at most 3 entries, '
    "empty if the message is emotionally neutral), "
    '"valence" (-1..1), "arousal" (0..1), "confidence" (0..1). '
    "Use only these emotion names: {names}. "
    "Judge the speaker's own feelings, respect negation, and do not invent emotion "
    "that is not expressed."
)


class GeminiEmotionAnalyzer:
    """Semantic analyzer that asks a google-genai client for structured JSON."""

    def __init__(self, client: Any, model: str = DEFAULT_CLOUD_MODEL) -> None:
        if client is None:
            raise ValueError("cloud emotion analysis needs a configured Gemini client")
        self.client = client
        self.model = model
        names = ", ".join(sorted(n for n in _SPECS if n != "neutral"))
        self.instruction = _CLOUD_INSTRUCTION.format(names=names)

    def __call__(self, *, text: str, entity: str, context: Mapping) -> dict:
        response = self.client.models.generate_content(
            model=self.model,
            contents=text,
            config={
                "system_instruction": self.instruction,
                "response_mime_type": "application/json",
                "temperature": 0,
            },
        )
        payload = json.loads(response.text or "")
        if not isinstance(payload, Mapping):
            raise ValueError("cloud emotion analysis did not return a JSON object")
        # Keep only the observation contract; models sometimes add commentary keys.
        cleaned = {key: payload[key] for key in _OBSERVATION_KEYS if key in payload}
        emotions = cleaned.get("emotions") or {}
        if not isinstance(emotions, Mapping):
            raise ValueError("cloud emotion analysis returned non-object emotions")
        cleaned["emotions"] = {str(k).lower(): float(v) for k, v in emotions.items()}
        cleaned["source"] = "cloud"
        return cleaned


def _provider_choice(name: str | None) -> str:
    name = (name or "rule").strip().lower()
    if name not in PROVIDER_CHOICES:
        log.warning("Unknown emotion provider %r; using rule-based provider. Choices: %s",
                    name, ", ".join(PROVIDER_CHOICES))
        return "rule"
    return name


def configured_provider_name(env: Mapping[str, str] | None = None) -> str:
    """Provider name from ``AEGIS_EMOTION_PROVIDER`` alone (no config file)."""
    env = os.environ if env is None else env
    return _provider_choice(env.get(PROVIDER_ENV))


def build_providers(*, config: EmotionConfig | None = None, cloud_client: Any = None,
                    local_pipeline_factory: Callable | None = None) -> dict[str, EmotionProvider]:
    """Build every provider that can be constructed in this environment."""
    config = config or EmotionConfig()
    providers: dict[str, EmotionProvider] = {"rule": RuleBasedEmotionProvider()}
    providers["local"] = LocalSemanticEmotionProvider(LocalClassifierAnalyzer(
        config.local_model, pipeline_factory=local_pipeline_factory))
    if cloud_client is not None:
        providers["cloud"] = CloudSemanticEmotionProvider(GeminiEmotionAnalyzer(
            cloud_client, config.cloud_model))
    return providers


def select_provider(name: str, providers: Mapping[str, EmotionProvider]) -> tuple[str, EmotionProvider]:
    """Resolve a configured name to a provider, degrading to rules when unavailable."""
    if name == "auto":
        chain = [providers[key] for key in ("local", "cloud") if key in providers]
        if chain:
            return "auto", _NamedFallback(chain, name="auto")
        return "rule", providers["rule"]
    if name in providers:
        return name, providers[name]
    log.warning("Emotion provider %r is unavailable (no Gemini client configured?); "
                "using rule-based provider.", name)
    return "rule", providers["rule"]


class _NamedFallback(FallbackEmotionProvider):
    def __init__(self, providers, *, name: str) -> None:
        super().__init__(providers)
        self.name = name


def build_emotion_engine(*, config: EmotionConfig | None = None,
                         env: Mapping[str, str] | None = None, cloud_client: Any = None,
                         event_bus: EventBus | None = None,
                         local_pipeline_factory: Callable | None = None,
                         **engine_kwargs) -> EmotionEngine:
    """Create an EmotionEngine from ``mind/emotion.yaml`` plus env overrides.

    ``config`` defaults to :func:`load_emotion_config` (``env`` is passed to
    it). Explicit ``engine_kwargs`` win over config values, which keeps tests
    and callers able to pin a parameter such as ``clock``.

    All constructible providers are registered, so callers can still switch at
    runtime with ``engine.use_provider(name)`` or ``process(..., provider=name)``.
    Semantic provider failures at analysis time fall back to rules and emit
    ``emotion.provider_fallback`` (handled inside ``EmotionEngine.process``).
    """
    config = config or load_emotion_config(env=env)
    providers = build_providers(config=config, cloud_client=cloud_client,
                                local_pipeline_factory=local_pipeline_factory)
    name, provider = select_provider(_provider_choice(config.provider), providers)
    providers[name] = provider
    kwargs = {**config.engine_kwargs(),
              "regulation": EmotionalRegulation(**config.regulation_kwargs()),
              **engine_kwargs}
    engine = EmotionEngine(provider, providers=providers, event_bus=event_bus, **kwargs)
    engine.provider_name = name
    for emotion, half_life in config.half_lives.items():
        if emotion in engine.vocabulary:
            engine.register_emotion(replace(engine.vocabulary.get(emotion), half_life_seconds=half_life))
        else:
            log.warning("emotion config: half_lives.%s is not a known emotion; ignoring it", emotion)
    engine.config = config
    return engine
