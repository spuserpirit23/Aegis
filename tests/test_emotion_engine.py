import json

import pytest

from brain.emotion_engine import (
    CloudSemanticEmotionProvider,
    EmotionEngine,
    EmotionObservation,
    EmotionState,
    EmotionSpec,
    EmotionalRegulation,
    RuleBasedEmotionProvider,
    LocalSemanticEmotionProvider,
    legacy_label,
    main,
)
from brain.event_bus import EventBus


class Clock:
    def __init__(self, now=1_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class StaticProvider:
    name = "static"

    def __init__(self, emotions):
        self.emotions = emotions

    def analyze(self, text, *, entity="user", context=None):
        return EmotionObservation(entity, dict(self.emotions), valence=-.5,
                                  arousal=.5, confidence=1.0, source=self.name)


class BrokenProvider:
    name = "broken"

    def analyze(self, text, *, entity="user", context=None):
        raise RuntimeError("provider offline")


def test_data_models_normalize_and_represent_valid_emotion_data():
    observation = EmotionObservation(
        "  user:demo  ", {"Happy": .7, "sad": .2, "neutral": 0},
        valence=.4, arousal=.6, dominance=.55, confidence=.8, source="test",
    )
    state = EmotionState("aegis", {"calm": .6, "focused": .3}, valence=.1)

    assert observation.entity == "user:demo"
    assert observation.emotions == {"happy": .7, "sad": .2}
    assert observation.primary == "happy"
    assert observation.intensity == .7
    assert state.primary == "calm"
    assert state.secondary == {"focused": .3}
    assert state.to_dict()["valence"] == .1


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EmotionSpec("invalid", float("nan"), .5),
        lambda: EmotionSpec("", 0, .5),
        lambda: EmotionSpec("sad", -.7, .3, half_life_seconds=0),
        lambda: EmotionObservation("user", {"sad": float("inf")}),
        lambda: EmotionObservation("user", {"sad": .5}, confidence=float("nan")),
        lambda: EmotionState("aegis", {"calm": .5}, arousal=float("inf")),
    ],
)
def test_data_models_reject_non_finite_or_invalid_metadata(factory):
    with pytest.raises(ValueError):
        factory()


def test_rule_provider_detects_blended_evidence():
    obs = RuleBasedEmotionProvider().analyze("I'm furious and nothing works!")
    assert obs.primary in {"frustrated", "angry"}
    assert obs.emotions["frustrated"] > 0.5
    assert obs.emotions["angry"] > 0.5
    assert obs.valence < 0
    assert 0 < obs.confidence <= 1


def test_plain_text_is_low_intensity_evidence():
    obs = RuleBasedEmotionProvider().analyze("The library opens at nine.")
    assert obs.emotions == {}
    assert obs.intensity == 0
    assert obs.valence == 0


def test_negated_emotion_is_not_detected():
    obs = RuleBasedEmotionProvider().analyze("I am not angry at all.")
    assert "angry" not in obs.emotions


def test_perception_does_not_become_aegis_state():
    engine = EmotionEngine()
    observation = engine.process("I am furious!", entity="user:arpan")
    state = engine.state("user:arpan")
    assert observation.entity == "user:arpan"
    assert state.primary == "angry"
    # AEGIS itself has not been updated merely by perceiving the user.
    assert engine.state("aegis").emotions == {}
    assert engine.state("aegis").primary == "neutral"


def test_regulation_does_not_mirror_anger():
    engine = EmotionEngine()
    observation = engine.process("I am furious!", entity="user")
    state = engine.regulate(observation)
    assert "angry" not in state.emotions
    assert state.primary in {"concerned", "calm"}
    assert state.emotions["concerned"] >= 0.5
    assert state.emotions["calm"] > 0


def test_multiple_emotions_coexist_in_state():
    engine = EmotionEngine()
    engine.process("I'm frustrated and a bit sad.", entity="user")
    state = engine.state("user")
    assert len(state.emotions) >= 2
    assert state.secondary  # a blend, not a winner-takes-all label


def test_valence_and_arousal_are_independent_of_primary_label():
    engine = EmotionEngine()
    engine.process("I'm terrified.", entity="user")
    state = engine.state("user")
    assert state.primary == "afraid"
    assert -1 <= state.valence <= 1
    assert 0 <= state.arousal <= 1
    assert state.valence < 0 and state.arousal > 0.5


def test_repeated_evidence_sustains_state():
    engine = EmotionEngine()
    first = engine.process("I'm frustrated.", entity="user")
    first_state = engine.state("user").emotions["frustrated"]
    engine.process("Still frustrated.", entity="user")
    second_state = engine.state("user").emotions["frustrated"]
    assert second_state >= first_state * 0.9
    assert first.emotions["frustrated"] == 1.0 - 1e-9 or first.emotions["frustrated"] > 0.5


def test_state_decays_toward_neutral_without_evidence():
    clock = Clock()
    engine = EmotionEngine(clock=clock)
    engine.process("I'm furious.", entity="user")
    before = engine.state("user").intensity
    assert engine.state("user").valence < 0

    clock.advance(3600)
    after = engine.state("user")
    assert after.intensity < before
    assert after.valence > -0.2


def test_fast_and_slow_emotions_decay_differently():
    clock = Clock()
    engine = EmotionEngine(clock=clock)
    engine.update(EmotionObservation("user", {"surprised": 1.0, "sad": 1.0}))
    clock.advance(120)
    state = engine.state("user")
    assert state.emotions["sad"] > state.emotions["surprised"] * 3


def test_entities_are_tracked_separately():
    engine = EmotionEngine()
    engine.process("I'm thrilled!", entity="user:a")
    angry = engine.process("I'm furious!", entity="user:b")
    assert engine.state("user:a").primary in {"happy", "joyful", "excited"}
    assert engine.state("user:b").primary == "angry"
    assert angry.entity == "user:b"


def test_state_change_emits_event():
    engine = EmotionEngine()
    seen = []
    engine.on("emotion.changed", seen.append)
    engine.process("I'm frustrated.", entity="user")
    assert seen
    assert seen[-1]["current"] == "frustrated"
    assert seen[-1]["previous"] == "neutral"


def test_process_records_detection_state_and_primary_change_events():
    engine = EmotionEngine()
    engine.process("I'm frustrated.", entity="user")
    events = engine.events()

    assert [event["event"] for event in events] == [
        "emotion.detected", "emotion.changed", "emotion.state_changed",
    ]
    assert events[1]["entity"] == "user"
    assert events[1]["previous"] == "neutral"
    assert events[1]["current"] == "frustrated"


def test_history_is_bounded_and_entity_scoped():
    engine = EmotionEngine(history_limit=3)
    for _ in range(5):
        engine.process("I'm frustrated.", entity="user")
    assert len(engine.history("user")) == 3
    assert engine.history("aegis") == []


def test_unknown_labels_can_be_registered_without_editing_engine():
    engine = EmotionEngine(provider=StaticProvider({"wistful": .9}))
    engine.register_emotion(EmotionSpec("wistful", -.3, .3))
    engine.process("some text", entity="user")
    assert engine.state("user").primary == "wistful"


def test_provider_can_be_replaced_without_changing_engine():
    engine = EmotionEngine(provider=StaticProvider({"proud": .8}))
    engine.process("anything", entity="user")
    assert engine.state("user").primary == "proud"
    assert engine.provider_name == "static"


def test_failing_provider_falls_back_to_rules():
    engine = EmotionEngine(provider=BrokenProvider())
    obs = engine.process("I'm frustrated.", entity="user")
    assert obs.source == "rule"
    assert engine.state("user").primary == "frustrated"
    assert any(e["event"] == "emotion.provider_fallback" for e in engine.events())


def test_model_adapters_normalize_structured_json_and_uncertainty():
    local = LocalSemanticEmotionProvider(
        lambda **kwargs: '{"emotions": {"anxious": 0.8}, "uncertainty": 0.25, "valence": -0.6, "arousal": 0.7}'
    )
    cloud = CloudSemanticEmotionProvider(
        lambda **kwargs: {"emotions": {"happy": .8}, "confidence": .9, "valence": .6, "arousal": .5}
    )
    assert local.analyze("x").confidence == .75
    assert local.analyze("x").uncertainty == .25
    assert cloud.analyze("x").source == "cloud"


def test_engine_publishes_emotion_events_to_shared_event_bus():
    bus = EventBus()
    seen = []
    bus.subscribe("emotion.changed", seen.append)
    engine = EmotionEngine(event_bus=bus)
    engine.process("I'm frustrated.", entity="user")
    assert seen[-1]["current"] == "frustrated"
    assert bus.events()[0]["event"] == "emotion.detected"


def test_rule_provider_is_used_without_any_llm():
    engine = EmotionEngine()
    obs = engine.process("I've been trying this for hours and nothing works!")
    assert obs.source == "rule"
    assert engine.state("user").intensity > 0.4


def test_acceptance_demo_produces_concerned_and_calm():
    engine = EmotionEngine()
    observation = engine.process("I've been trying this for hours and nothing works!", entity="user")
    state = engine.regulate(observation)
    assert observation.primary == "frustrated"
    assert observation.valence < -0.5
    assert state.primary == "concerned"
    assert state.emotions["concerned"] >= .6
    assert state.emotions["calm"] >= .5
    assert "frustrated" not in state.emotions


def test_legacy_labels_stay_within_existing_prompt_vocabulary():
    allowed = {"happy", "thinking", "concerned", "neutral"}
    for name in ("joyful", "proud", "anxious", "confused", "curious", "calm"):
        engine = EmotionEngine(provider=StaticProvider({name: .8}))
        engine.process("x", entity="aegis")
        assert legacy_label(engine.state("aegis")) in allowed


def test_regulation_is_configurable_and_never_returns_observed_anger():
    regulation = EmotionalRegulation(concern_base=.1, concern_gain=.2)
    observation = EmotionObservation("user", {"angry": .9}, valence=-.8, arousal=.9, confidence=.9)
    state = regulation.regulate(observation)
    assert "angry" not in state.emotions
    assert state.emotions["concerned"] < .5
    assert state.arousal < observation.arousal


def test_cli_demo_emits_machine_readable_state(capsys):
    assert main(["I am furious and nothing works", "--entity", "user:demo", "--regulate", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["observation"]["entity"] == "user:demo"
    assert output["observation"]["primary"] in {"angry", "frustrated"}
    assert output["aegis_state"]["primary"] in {"concerned", "calm"}
