import inspect
import logging
import textwrap

import pytest

from brain.emotion_config import (
    DEFAULT_CONFIG_PATH,
    EmotionConfig,
    EngineTuning,
    RegulationTuning,
    load_emotion_config,
)
from brain.emotion_engine import EmotionEngine, EmotionalRegulation
from brain.emotion_providers import build_emotion_engine


class Clock:
    def __init__(self, now=1_000.0):
        self.now = now

    def __call__(self):
        return self.now


def write(tmp_path, text):
    path = tmp_path / "emotion.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def signature_defaults(func):
    return {name: p.default for name, p in inspect.signature(func).parameters.items()
            if p.default is not inspect.Parameter.empty}


def test_shipped_yaml_matches_built_in_defaults():
    config = load_emotion_config(DEFAULT_CONFIG_PATH, env={})
    assert config.source == str(DEFAULT_CONFIG_PATH)
    assert config.engine == EngineTuning()
    assert config.regulation == RegulationTuning()
    assert config.provider == "rule"
    assert dict(config.half_lives) == {}


def test_tuning_defaults_match_engine_constructor_defaults():
    engine_defaults = signature_defaults(EmotionEngine.__init__)
    for name, value in EmotionConfig().engine_kwargs().items():
        assert engine_defaults[name] == value, name
    regulation_defaults = signature_defaults(EmotionalRegulation.__init__)
    for name, value in EmotionConfig().regulation_kwargs().items():
        assert regulation_defaults[name] == value, name


def test_missing_file_gives_defaults(tmp_path):
    config = load_emotion_config(tmp_path / "nope.yaml", env={})
    assert config == EmotionConfig()
    assert config.source == "defaults"


def test_yaml_values_reach_the_engine(tmp_path):
    path = write(tmp_path, """
        engine:
          persistence: 0.5
          decay: false
          dimension_half_life_seconds: 60
          history_limit: 7
          event_limit: 9
        regulation:
          concern_base: 0.1
          concern_gain: 0.2
          calm_base: 0.3
          calm_under_stress: 0.4
        half_lives:
          Frustrated: 30
    """)
    engine = build_emotion_engine(config=load_emotion_config(path, env={}))
    assert engine.persistence == .5
    assert engine.decay_enabled is False
    assert engine.dimension_half_life_seconds == 60
    assert engine.history_limit == 7
    assert engine.event_limit == 9
    reg = engine.regulator
    assert (reg.concern_base, reg.concern_gain, reg.calm_base, reg.calm_under_stress) == (.1, .2, .3, .4)
    assert engine.vocabulary.get("frustrated").half_life_seconds == 30
    assert engine.config.source == str(path)


def test_env_overrides_yaml(tmp_path):
    path = write(tmp_path, """
        provider: local
        cloud_model: from-yaml
    """)
    config = load_emotion_config(path, env={"AEGIS_EMOTION_PROVIDER": "auto",
                                            "AEGIS_EMOTION_CLOUD_MODEL": "from-env"})
    assert config.provider == "auto"
    assert config.cloud_model == "from-env"
    assert load_emotion_config(path, env={}).provider == "local"


def test_blank_env_does_not_override(tmp_path):
    path = write(tmp_path, "provider: local\n")
    assert load_emotion_config(path, env={"AEGIS_EMOTION_PROVIDER": "  "}).provider == "local"


def test_config_path_from_env(tmp_path):
    path = write(tmp_path, "engine: {persistence: 0.4}\n")
    config = load_emotion_config(env={"AEGIS_EMOTION_CONFIG": str(path)})
    assert config.engine.persistence == .4


@pytest.mark.parametrize("snippet, key", [
    ("engine: {persistence: 1.5}", "persistence"),
    ("engine: {persistence: high}", "persistence"),
    ("engine: {decay: 'yes'}", "decay"),
    ("engine: {history_limit: 0}", "history_limit"),
    ("engine: {history_limit: 2.5}", "history_limit"),
    ("engine: {event_limit: true}", "event_limit"),
    ("engine: {dimension_half_life_seconds: -5}", "dimension_half_life_seconds"),
    ("regulation: {concern_gain: -0.1}", "concern_gain"),
])
def test_invalid_values_warn_and_keep_default(tmp_path, caplog, snippet, key):
    path = write(tmp_path, snippet + "\n")
    with caplog.at_level(logging.WARNING, logger="brain.emotion_config"):
        config = load_emotion_config(path, env={})
    assert config.engine == EngineTuning() and config.regulation == RegulationTuning()
    assert key in caplog.text


def test_unknown_keys_warn(tmp_path, caplog):
    path = write(tmp_path, """
        persistance: 0.5
        engine: {persitence: 0.5}
    """)
    with caplog.at_level(logging.WARNING, logger="brain.emotion_config"):
        config = load_emotion_config(path, env={})
    assert config.engine == EngineTuning()
    assert "persistance" in caplog.text
    assert "engine.persitence" in caplog.text


@pytest.mark.parametrize("text", ["engine: [unclosed\n", "- just\n- a list\n", "", "engine: 5\n"])
def test_malformed_files_fall_back_to_defaults(tmp_path, text):
    config = load_emotion_config(write(tmp_path, text), env={})
    assert config.engine == EngineTuning()


def test_bad_half_life_entries_are_ignored(tmp_path, caplog):
    path = write(tmp_path, """
        half_lives:
          sad: -1
          wistful: 100
          angry: 60
    """)
    with caplog.at_level(logging.WARNING):
        engine = build_emotion_engine(config=load_emotion_config(path, env={}))
    assert engine.vocabulary.get("angry").half_life_seconds == 60
    assert engine.vocabulary.get("sad").half_life_seconds == 1800  # built-in kept
    assert "wistful" in caplog.text


def test_explicit_kwargs_win_over_config(tmp_path):
    path = write(tmp_path, "engine: {persistence: 0.4}\n")
    engine = build_emotion_engine(config=load_emotion_config(path, env={}), persistence=.9)
    assert engine.persistence == .9


def test_emotion_half_life_override_changes_decay_speed(tmp_path):
    def remaining(config):
        clock = Clock()
        engine = build_emotion_engine(config=config, clock=clock)
        engine.process("I'm frustrated", entity="user")
        start = engine.state("user").emotions["frustrated"]
        clock.now += 600
        return engine.state("user").emotions.get("frustrated", 0) / start

    fast = load_emotion_config(write(tmp_path, "half_lives: {frustrated: 600}\n"), env={})
    assert remaining(EmotionConfig()) == pytest.approx(.5 ** (600 / 1200))
    assert remaining(fast) == pytest.approx(.5)


def test_dimension_half_life_changes_valence_drift(tmp_path):
    def valence_after(config):
        clock = Clock()
        engine = build_emotion_engine(config=config, clock=clock)
        engine.process("I'm frustrated", entity="user")
        start = engine.state("user").valence
        clock.now += 300
        return engine.state("user").valence / start

    fast = load_emotion_config(write(tmp_path, "engine: {dimension_half_life_seconds: 300}\n"), env={})
    assert valence_after(fast) == pytest.approx(.5)
    assert valence_after(EmotionConfig()) == pytest.approx(.5 ** (300 / 900))


def test_regulation_tuning_changes_aegis_response(tmp_path):
    strong = load_emotion_config(write(tmp_path, "regulation: {concern_base: 0.6, concern_gain: 0.4}\n"), env={})
    default_engine = build_emotion_engine(config=EmotionConfig())
    strong_engine = build_emotion_engine(config=strong)
    text = "I'm frustrated, nothing works"
    default = default_engine.regulate(default_engine.process(text)).emotions["concerned"]
    tuned = strong_engine.regulate(strong_engine.process(text)).emotions["concerned"]
    assert tuned > default
