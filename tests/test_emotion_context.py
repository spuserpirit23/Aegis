import dataclasses
import json
import os
import tempfile
from unittest.mock import patch

import pytest

from brain.emotion_context import AegisEmotion, ObservedUserEmotion, ResponseEmotionContext
from brain.emotion_engine import EmotionEngine


def turn_context(engine, text, *, entity="user:sam", label=None, trust="medium"):
    observation = engine.process(text, entity=entity)
    aegis = engine.regulate(observation)
    return ResponseEmotionContext.from_turn(
        observation=observation, user_state=engine.state(entity), aegis_state=aegis,
        label=label or aegis.primary, trust=trust)


@pytest.fixture
def planner():
    from brain.memory import Memory
    from brain.planner import Planner

    with tempfile.NamedTemporaryFile(delete=False) as handle:
        path = handle.name
    with patch("brain.planner.load_env_file", return_value={}), \
            patch.dict(os.environ, {"AEGIS_EMOTION_PROVIDER": "rule"}):
        os.environ.pop("GEMINI_API_KEY", None)
        yield Planner(Memory(path=path))
    if os.path.exists(path):
        os.remove(path)


def test_baseline_has_no_observation_and_no_prompt_block():
    ctx = ResponseEmotionContext.baseline()
    assert ctx.observed is False
    assert ctx.user is None
    assert ctx.aegis.source == "baseline"
    assert ctx.to_prompt() == ""


def test_from_turn_separates_user_evidence_from_aegis_tendency():
    ctx = turn_context(EmotionEngine(), "I've been trying for hours and nothing works!")
    assert isinstance(ctx.user, ObservedUserEmotion)
    assert isinstance(ctx.aegis, AegisEmotion)
    assert ctx.user.emotion == "frustrated"
    assert ctx.user.turn_emotion == "frustrated"
    assert ctx.user.source == "rule"
    assert ctx.aegis.tendency == "concerned"  # regulated, not mirrored
    assert ctx.user.uncertainty == pytest.approx(1 - ctx.user.confidence)


def test_turn_evidence_and_blended_state_can_differ():
    engine = EmotionEngine()
    for _ in range(3):
        engine.process("I'm so frustrated, nothing works", entity="user:sam")
    ctx = turn_context(engine, "ok", entity="user:sam")
    assert ctx.user.turn_emotion == "neutral"  # this sentence alone
    assert ctx.user.emotion == "frustrated"    # what persists across turns


def test_context_is_immutable():
    ctx = turn_context(EmotionEngine(), "I'm worried and anxious")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.label = "happy"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.aegis.intensity = 1.0
    with pytest.raises(TypeError):
        ctx.aegis.secondary["calm"] = 1.0


def test_context_does_not_track_later_engine_changes():
    engine = EmotionEngine()
    ctx = turn_context(engine, "I'm frustrated")
    before = ctx.user.emotion
    engine.process("thank you, this is awesome", entity="user:sam")
    assert ctx.user.emotion == before


def test_prompt_block_format():
    ctx = turn_context(EmotionEngine(), "I'm frustrated")
    lines = ctx.to_prompt().splitlines()
    assert lines[0] == "[AFFECTIVE CONTEXT]"
    assert lines[1] == "observed_user_emotion: frustrated"
    assert lines[2].startswith("observed_confidence: ")
    assert lines[3].startswith("observed_uncertainty: ")
    assert lines[4] == "aegis_response_tendency: concerned"
    assert lines[5].startswith("aegis_intensity: ")
    assert "Do not claim certainty" in lines[6]


def test_to_dict_is_json_safe():
    ctx = turn_context(EmotionEngine(), "I'm frustrated")
    data = json.loads(json.dumps(ctx.to_dict()))
    assert data["user"]["turn_emotion"] == "frustrated"
    assert data["aegis"]["tendency"] == "concerned"
    assert isinstance(data["aegis"]["secondary"], dict)
    assert json.loads(json.dumps(ResponseEmotionContext.baseline().to_dict()))["user"] is None


def test_planner_starts_with_baseline(planner):
    assert planner.emotion_context.observed is False
    planner._resolve_speaker("hello")
    assert "[AFFECTIVE CONTEXT]" not in planner._build_system_instruction()


def test_planner_prompt_uses_typed_context(planner):
    from brain.trust import trust_level

    planner.respond("I've been trying this for hours and nothing works!")
    ctx = planner.emotion_context
    assert isinstance(ctx, ResponseEmotionContext)
    assert ctx.label == planner.last_emotion
    assert ctx.trust == trust_level(planner.current_user)
    assert ctx.to_prompt() in planner._build_system_instruction()


@pytest.mark.parametrize("trust", ["low", "high"])
def test_planner_low_trust_label_suppresses_warmth(planner, trust):
    from brain.emotion_engine import legacy_label

    with patch("brain.planner.trust_level", return_value=trust):
        planner.respond("thanks, that worked great")
    ctx = planner.emotion_context
    engine_label = legacy_label(planner.last_emotion_state)
    assert ctx.trust == trust
    if trust == "low":
        assert ctx.label != "happy"
    else:
        assert ctx.label == engine_label


# Hardcoded display claims (expressiveness, voice_pitch_modulation, persona,
# status) were removed; "provider" reports the real emotion provider.
BRIDGE_KEYS = {
    "emotion", "intensity", "last_user_input", "valence", "valence_score", "arousal",
    "dominance", "confidence", "secondary", "source", "user_emotion", "user_valence",
    "context", "provider",
}


def test_bridge_emotion_payload_is_backward_compatible(planner):
    from aegis_bridge import AegisBridge

    baseline = AegisBridge(planner).emotion_state()
    assert set(baseline) == BRIDGE_KEYS
    assert baseline["source"] == "baseline"
    assert baseline["user_emotion"] is None and baseline["user_valence"] is None

    planner.respond("I'm so frustrated, nothing works")
    payload = AegisBridge(planner).emotion_state()
    assert set(payload) == BRIDGE_KEYS
    assert payload["emotion"] == planner.last_emotion_state.primary.upper()
    assert payload["user_emotion"] == planner.last_observation.primary
    assert payload["user_valence"] == round(planner.last_observation.valence, 3)
    json.dumps(payload)
