"""Event contract tests: every emitted payload must match EMOTION_EVENTS."""
import json

from brain.emotion_engine import (
    EMOTION_EVENTS,
    EmotionEngine,
    EmotionObservation,
    EmotionState,
    FallbackEmotionProvider,
    SemanticEmotionProvider,
)
from brain.emotion_providers import build_emotion_engine
from brain.event_bus import EventBus


class Clock:
    def __init__(self, now=1_000.0):
        self.now = now

    def __call__(self):
        return self.now


class Broken:
    def __init__(self, name="broken", error=RuntimeError("model offline")):
        self.name, self.error = name, error

    def analyze(self, text, *, entity="user", context=None):
        raise self.error


def exercise_every_event(engine, clock):
    """Drive one engine through every event type it can emit."""
    engine.process("I'm frustrated, nothing works", entity="user")  # detected, changed, state_changed
    engine.use_provider("broken")
    engine.process("thanks, that's great", entity="user")            # provider_fallback
    clock.now += 100_000                                              # long enough to decay to neutral
    engine.state("user")                                              # decayed + changed(cause=decay)


def make_engine(clock, **kwargs):
    engine = EmotionEngine(clock=clock, **kwargs)
    engine.register_provider("broken", Broken())
    return engine


def test_every_documented_event_is_emitted():
    clock = Clock()
    engine = make_engine(clock)
    exercise_every_event(engine, clock)
    emitted = {event["event"] for event in engine.events()}
    assert emitted == set(EMOTION_EVENTS)


def test_payload_keys_match_documented_contract_exactly():
    clock = Clock()
    engine = make_engine(clock)
    exercise_every_event(engine, clock)
    for event in engine.events():
        name = event.pop("event")
        assert set(event) == set(EMOTION_EVENTS[name]), name


def test_event_bus_receives_same_contract():
    clock, bus = Clock(), EventBus()
    engine = make_engine(clock, event_bus=bus)
    exercise_every_event(engine, clock)
    local = engine.events()
    published = bus.events()
    assert [e["event"] for e in published] == [e["event"] for e in local]
    for event in published:
        name = event.pop("event")
        assert set(event) == set(EMOTION_EVENTS[name])


def test_payload_value_types():
    clock = Clock()
    engine = make_engine(clock)
    exercise_every_event(engine, clock)
    for event in engine.events():
        assert isinstance(event["entity"], str)
        assert event["timestamp"] == clock.now or event["timestamp"] == 1_000.0
        if "state" in event:
            assert isinstance(event["state"], EmotionState)
        if event["event"] == "emotion.detected":
            assert isinstance(event["observation"], EmotionObservation)
        if event["event"] == "emotion.changed":
            assert event["cause"] in {"observation", "decay"}
            assert 0 <= event["intensity"] <= 1


def test_decay_that_changes_primary_emits_changed_with_decay_cause():
    clock = Clock()
    engine = make_engine(clock)
    engine.process("I'm frustrated", entity="user")
    clock.now += 100_000
    engine.state("user")
    changes = [e for e in engine.events() if e["event"] == "emotion.changed"]
    assert changes[-1]["cause"] == "decay"
    assert changes[-1]["previous"] == "frustrated"
    assert changes[-1]["current"] == "neutral"


def test_small_decay_does_not_claim_a_primary_change():
    clock = Clock()
    engine = make_engine(clock)
    engine.process("I'm frustrated", entity="user")
    clock.now += 60
    engine.state("user")
    causes = [e["cause"] for e in engine.events() if e["event"] == "emotion.changed"]
    assert causes == ["observation"]


def test_fallback_event_reports_error_and_replacement():
    clock = Clock()
    engine = make_engine(clock)
    engine.use_provider("broken")
    engine.process("I'm worried", entity="user")
    [event] = [e for e in engine.events() if e["event"] == "emotion.provider_fallback"]
    assert event["provider"] == "broken"
    assert event["fallback"] == "rule"
    assert event["error"] == "RuntimeError: model offline"


def test_fallback_error_is_truncated():
    engine = EmotionEngine(provider=Broken(error=ValueError("x" * 1000)))
    engine.process("hello")
    [event] = [e for e in engine.events() if e["event"] == "emotion.provider_fallback"]
    assert len(event["error"]) == 200


def test_chained_provider_failures_are_no_longer_silent():
    working = SemanticEmotionProvider(lambda **_: {"emotions": {"calm": .6}}, name="cloud")
    chain = FallbackEmotionProvider([Broken("local", ImportError("no transformers")), working])
    engine = EmotionEngine(provider=chain)
    observation = engine.process("all good")

    assert observation.source == "cloud"
    [event] = [e for e in engine.events() if e["event"] == "emotion.provider_fallback"]
    assert event["provider"] == "local"
    assert event["fallback"] == "cloud"
    assert event["error"].startswith("ImportError")


def test_fully_failed_chain_reports_each_failure():
    chain = FallbackEmotionProvider([Broken("local"), Broken("cloud")])
    engine = EmotionEngine(provider=chain)
    assert engine.process("I'm worried").source == "rule:fallback"
    events = [e for e in engine.events() if e["event"] == "emotion.provider_fallback"]
    assert [(e["provider"], e["fallback"]) for e in events] == [
        ("local", "rule:fallback"), ("cloud", "rule:fallback")]


def test_failures_do_not_leak_into_the_next_call():
    flaky_calls = []

    def flaky(**_):
        flaky_calls.append(1)
        if len(flaky_calls) == 1:
            raise RuntimeError("first call fails")
        return {"emotions": {"calm": .6}}

    engine = EmotionEngine(provider=FallbackEmotionProvider([SemanticEmotionProvider(flaky, name="cloud")]))
    engine.process("one")
    engine.process("two")
    assert len([e for e in engine.events() if e["event"] == "emotion.provider_fallback"]) == 1


def test_auto_mode_reports_local_failure():
    class Models:
        def generate_content(self, **_):
            return type("R", (), {"text": json.dumps({"emotions": {"curious": .7}})})()

    def broken_pipeline(*_, **__):
        raise ImportError("no transformers")

    engine = build_emotion_engine(env={"AEGIS_EMOTION_PROVIDER": "auto"},
                                  cloud_client=type("C", (), {"models": Models()})(),
                                  local_pipeline_factory=broken_pipeline)
    engine.process("how does this work")
    [event] = [e for e in engine.events() if e["event"] == "emotion.provider_fallback"]
    assert (event["provider"], event["fallback"]) == ("local", "cloud")


def test_detected_observation_is_a_snapshot():
    engine = EmotionEngine()
    returned = engine.process("I'm frustrated", entity="user")
    [detected] = [e for e in engine.events() if e["event"] == "emotion.detected"]
    returned.emotions["frustrated"] = 0.0
    assert detected["observation"].emotions["frustrated"] > 0


def test_engine_event_log_is_bounded():
    engine = EmotionEngine(event_limit=5)
    for _ in range(10):
        engine.process("I'm frustrated", entity="user")
    events = engine.events()
    assert len(events) == 5
    assert events[-1]["event"] == "emotion.state_changed"
