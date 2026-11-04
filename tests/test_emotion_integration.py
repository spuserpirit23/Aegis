import os
import tempfile
from unittest.mock import patch

from brain.memory import Memory
from brain.planner import Planner


def _offline_planner(path):
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GEMINI_API_KEY", None)
        with patch("brain.planner.load_env_file", return_value={}):
            return Planner(Memory(path=path))


def test_planner_updates_perception_and_regulated_state_without_llm():
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        path = handle.name
    try:
        planner = _offline_planner(path)
        planner.respond("I've been trying this for hours and nothing works!")

        assert planner.last_observation.primary == "frustrated"
        assert planner.last_emotion_state.primary == "concerned"
        assert planner.last_emotion == "concerned"
        assert "frustrated" not in planner.last_emotion_state.emotions

        user_state = planner.emotion_engine.state("user:guest")
        assert user_state.primary == "frustrated"
        assert user_state.valence < 0
        prompt = planner._build_system_instruction()
        assert "[AFFECTIVE CONTEXT]" in prompt
        assert "observed_uncertainty:" in prompt
        assert "aegis_response_tendency: concerned" in prompt
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_bridge_exposes_structured_emotion_state():
    from aegis_bridge import AegisBridge

    with tempfile.NamedTemporaryFile(delete=False) as handle:
        path = handle.name
    try:
        planner = _offline_planner(path)
        planner.respond("thanks, that worked great")
        payload = AegisBridge(planner).emotion_state()

        assert payload["status"] == "ONLINE"
        assert payload["emotion"] in {"HAPPY", "NEUTRAL", "CONCERNED"}
        assert 0 <= payload["intensity"] <= 1
        assert payload["valence"] in {"POSITIVE", "NEGATIVE", "NEUTRAL"}
        assert -1 <= payload["valence_score"] <= 1
        assert 0 <= payload["arousal"] <= 1
        assert payload["user_emotion"] == "happy"
        assert payload["source"] in {"regulation", "baseline"}
        debug = AegisBridge(planner).emotion_debug_state()
        assert debug["user_history"]
        assert debug["aegis_history"]
        assert any(event["event"] == "emotion.detected" for event in debug["bus_events"])
    finally:
        if os.path.exists(path):
            os.remove(path)
