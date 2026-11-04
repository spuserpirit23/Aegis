import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from brain.emotion_engine import RuleBasedEmotionProvider
from brain.emotion_providers import (
    GeminiEmotionAnalyzer,
    LocalClassifierAnalyzer,
    build_emotion_engine,
    configured_provider_name,
)


class FakeModels:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def fake_client(text=None, error=None):
    return SimpleNamespace(models=FakeModels(text, error))


def fake_pipeline_factory(scores=None, error=None):
    loads = []

    def factory(task, model, top_k):
        loads.append(model)
        if error:
            raise error
        return lambda text: [scores]

    factory.loads = loads
    return factory


LOCAL_SCORES = [
    {"label": "joy", "score": .91},
    {"label": "neutral", "score": .05},
    {"label": "sadness", "score": .02},
]


def test_default_is_rule_provider():
    engine = build_emotion_engine(env={})
    assert engine.provider_name == "rule"
    assert isinstance(engine.provider, RuleBasedEmotionProvider)


@pytest.mark.parametrize("value", ["bogus", "", "  "])
def test_unknown_or_empty_name_degrades_to_rule(value):
    assert configured_provider_name({"AEGIS_EMOTION_PROVIDER": value}) == "rule"


def test_name_is_case_insensitive():
    assert configured_provider_name({"AEGIS_EMOTION_PROVIDER": " Cloud "}) == "cloud"


def test_cloud_without_client_degrades_to_rule():
    engine = build_emotion_engine(env={"AEGIS_EMOTION_PROVIDER": "cloud"}, cloud_client=None)
    assert engine.provider_name == "rule"


def test_cloud_provider_parses_structured_json():
    client = fake_client(json.dumps({
        "emotions": {"Frustrated": .8}, "valence": -.6, "arousal": .7,
        "confidence": .85, "explanation": "ignored extra key",
    }))
    engine = build_emotion_engine(env={"AEGIS_EMOTION_PROVIDER": "cloud"}, cloud_client=client)
    observation = engine.process("this keeps failing", entity="user:sam")

    assert engine.provider_name == "cloud"
    assert observation.primary == "frustrated"
    assert observation.source == "cloud"
    assert observation.confidence == pytest.approx(.85)
    call = client.models.calls[0]
    assert call["contents"] == "this keeps failing"
    assert call["config"]["response_mime_type"] == "application/json"


def test_cloud_model_is_configurable():
    client = fake_client(json.dumps({"emotions": {}}))
    engine = build_emotion_engine(
        env={"AEGIS_EMOTION_PROVIDER": "cloud", "AEGIS_EMOTION_CLOUD_MODEL": "custom-model"},
        cloud_client=client,
    )
    engine.process("hello")
    assert client.models.calls[0]["model"] == "custom-model"


@pytest.mark.parametrize("client", [
    fake_client(error=RuntimeError("network down")),
    fake_client(text="not json"),
    fake_client(text=json.dumps({"emotions": {"angry": "very"}})),  # non-numeric
    fake_client(text=json.dumps(["a", "list"])),
])
def test_cloud_failures_fall_back_to_rules_with_event(client):
    engine = build_emotion_engine(env={"AEGIS_EMOTION_PROVIDER": "cloud"}, cloud_client=client)
    observation = engine.process("nothing works and I'm frustrated", entity="user")

    assert observation.source == "rule"
    assert observation.primary == "frustrated"
    fallbacks = [e for e in engine.events() if e["event"] == "emotion.provider_fallback"]
    assert fallbacks and fallbacks[0]["provider"] == "cloud"


def test_local_provider_maps_classifier_labels():
    factory = fake_pipeline_factory(LOCAL_SCORES)
    engine = build_emotion_engine(env={"AEGIS_EMOTION_PROVIDER": "local"},
                                  local_pipeline_factory=factory)
    observation = engine.process("best day ever")

    assert engine.provider_name == "local"
    assert observation.source == "local"
    assert observation.primary == "happy"
    assert "neutral" not in observation.emotions
    assert "sad" not in observation.emotions  # below min_score
    assert observation.valence > 0


def test_local_classifier_loads_once():
    factory = fake_pipeline_factory(LOCAL_SCORES)
    analyzer = LocalClassifierAnalyzer(pipeline_factory=factory)
    analyzer(text="a", entity="user", context={})
    analyzer(text="b", entity="user", context={})
    assert len(factory.loads) == 1


def test_local_neutral_only_yields_empty_observation():
    factory = fake_pipeline_factory([{"label": "neutral", "score": .97}])
    observation = LocalClassifierAnalyzer(pipeline_factory=factory)(text="ok", entity="user", context={})
    assert observation.emotions == {}
    assert observation.primary == "neutral"


def test_local_missing_dependency_falls_back_at_analysis_time():
    factory = fake_pipeline_factory(error=ImportError("no transformers"))
    engine = build_emotion_engine(env={"AEGIS_EMOTION_PROVIDER": "local"},
                                  local_pipeline_factory=factory)
    observation = engine.process("I'm so worried")
    assert observation.source == "rule"
    assert observation.primary == "anxious"


def test_auto_prefers_local_then_cloud_then_rules():
    client = fake_client(json.dumps({"emotions": {"curious": .7}, "confidence": .8}))
    broken_local = fake_pipeline_factory(error=ImportError("no transformers"))
    engine = build_emotion_engine(env={"AEGIS_EMOTION_PROVIDER": "auto"}, cloud_client=client,
                                  local_pipeline_factory=broken_local)
    assert engine.provider_name == "auto"
    assert engine.process("how does this work").source == "cloud"

    dead = fake_client(error=RuntimeError("down"))
    engine = build_emotion_engine(env={"AEGIS_EMOTION_PROVIDER": "auto"}, cloud_client=dead,
                                  local_pipeline_factory=broken_local)
    assert engine.process("I'm so worried").source == "rule:fallback"


def test_all_constructed_providers_are_switchable_at_runtime():
    client = fake_client(json.dumps({"emotions": {"calm": .6}}))
    engine = build_emotion_engine(env={}, cloud_client=client)
    assert {"rule", "local", "cloud"} <= set(engine.providers)
    assert engine.process("whatever", provider="cloud").source == "cloud"


def test_gemini_analyzer_requires_client():
    with pytest.raises(ValueError):
        GeminiEmotionAnalyzer(None)


def test_planner_uses_configured_provider():
    from brain.memory import Memory
    from brain.planner import Planner

    with tempfile.NamedTemporaryFile(delete=False) as handle:
        path = handle.name
    try:
        env = {"AEGIS_EMOTION_PROVIDER": "cloud"}
        with patch.dict(os.environ, env), patch("brain.planner.load_env_file", return_value={}):
            os.environ.pop("GEMINI_API_KEY", None)
            planner = Planner(Memory(path=path))
        # No API key -> no client -> cloud is unavailable, planner still starts on rules.
        assert planner.emotion_engine.provider_name == "rule"
    finally:
        if os.path.exists(path):
            os.remove(path)
