"""Regression tests for identity confirmation, guest isolation, internal
state separation, /reset, planner locking and trust reporting."""
import json
import os
import tempfile
import threading
import time
from unittest.mock import patch

import pytest

from brain.memory import GUEST_KEY, Memory, extract_name, parse_confirmation, repair
from brain.trust import trust_breakdown


@pytest.fixture
def path():
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(p)
    yield p
    for candidate in (p, p + ".tmp"):
        if os.path.exists(candidate):
            os.remove(candidate)


def offline_planner(path):
    from brain.planner import Planner

    with patch("brain.planner.load_env_file", return_value={}), \
            patch.dict(os.environ, {"AEGIS_EMOTION_PROVIDER": "rule"}):
        os.environ.pop("GEMINI_API_KEY", None)
        return Planner(Memory(path=path))


# ------------------------------------------------------------ extraction

@pytest.mark.parametrize("text,expected", [
    ("my name is Jordan", "Jordan"),
    ("call me Jay", "Jay"),
    ("hey I'm Spirit", "Spirit"),
    ("hi, i am Arpan.", "Arpan"),
    ("this is Spirit speaking", "Spirit"),
    ("I am very happ today", None),          # produced the "very" user
    ("I'm very tired", None),
    ("I'm going to test you now", None),
    ("I am building your emotion engine", None),
    ("hey i am akshat, spirit's friend i am testing u", None),
    ("I'm not sure", None),
    ("what's the weather", None),
])
def test_extract_name_requires_explicit_introduction(text, expected):
    assert extract_name(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yeah that's me", True), ("correct", True),
    ("no", False), ("nope, it's Spirit", False), ("what's the weather", None),
])
def test_parse_confirmation(text, expected):
    assert parse_confirmation(text) is expected


# ------------------------------------------------------------ confirmation flow

def test_name_is_only_adopted_after_confirmation(path):
    planner = offline_planner(path)
    reply = planner.respond("hey I'm Spitit")
    assert planner.current_user.key == GUEST_KEY
    assert planner.pending_name == "Spitit"
    assert "Spitit" in reply
    assert not Memory(path=path).has_user("spitit")

    planner.respond("no, my name is Spirit")
    assert planner.current_user.key == GUEST_KEY
    assert planner.pending_name == "Spirit"

    planner.respond("yes")
    assert planner.current_user.key == "spirit"
    assert planner.pending_name is None
    stored = Memory(path=path)
    assert stored.has_user("spirit") and not stored.has_user("spitit")


def test_unrelated_reply_drops_pending_name(path):
    planner = offline_planner(path)
    planner.respond("my name is Parth")
    planner.respond("what's the weather like")
    assert planner.pending_name is None
    assert planner.current_user.key == GUEST_KEY


def test_switching_named_user_also_needs_confirmation(path):
    planner = offline_planner(path)
    planner.set_user("Spirit")
    planner.respond("my name is Parth")
    assert planner.current_user.key == "spirit"
    planner.respond("yes")
    assert planner.current_user.key == "parth"


def test_model_remember_name_for_guest_is_only_a_candidate(path):
    planner = offline_planner(path)
    result = planner._remember_fact("name", "Aashu")
    assert result["ok"] is False and result["pending_confirmation"] is True
    assert planner.current_user.key == GUEST_KEY
    assert planner.pending_name == "Aashu"


def test_my_name_is_does_not_overwrite_existing_users_name_fact(path):
    planner = offline_planner(path)
    planner.set_user("Spirit")
    planner.respond("my name is Parth")
    assert planner.current_user.facts["name"] == "Spirit"


# ------------------------------------------------------------ guest isolation

def test_guest_cannot_write_persistent_facts(path):
    planner = offline_planner(path)
    planner.respond("my favourite colour is blue")
    assert planner._remember_fact("project", "rover")["ok"] is False
    assert planner.current_user.facts == {}
    assert planner.current_user.remember_fact("x", "y") is False


def test_guest_bucket_is_never_written_to_disk(path):
    planner = offline_planner(path)
    planner.respond("hello there")
    planner.set_user("Sam")  # forces a save
    on_disk = json.loads(open(path, encoding="utf-8").read())
    assert GUEST_KEY not in on_disk["users"]
    assert "sam" in on_disk["users"]


def test_legacy_guest_bucket_and_internal_facts_are_migrated(path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"users": {
            "guest": {"turns": [{"role": "user", "content": "hi"}],
                      "facts": {"name": "Parth", "nickname": "Aashu"}},
            "spirit": {"turns": [], "facts": {"name": "spirit", "_last_interaction_id": "v1_x",
                                               "_correction_count": 2}},
        }, "last_user": "guest"}, fh)
    mem = Memory(path=path)
    assert GUEST_KEY not in mem.data["users"]
    spirit = mem.get_user("spirit")
    assert spirit.facts == {"name": "spirit"}
    assert spirit.state == {"_last_interaction_id": "v1_x", "_correction_count": 2}
    assert "_correction_count" not in spirit.facts
    assert trust_breakdown(spirit).corrections == 2


def test_repair_drops_named_buckets_and_backs_up(path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"users": {
            "guest": {"turns": [], "facts": {"name": "Parth"}},
            "very": {"turns": [], "facts": {"name": "very", "_last_interaction_id": "a"}},
            "spitit": {"turns": [], "facts": {"name": "spitit"}},
            "spirit": {"turns": [], "facts": {"name": "spirit", "_last_interaction_id": "b"}},
        }, "last_user": "very"}, fh)
    report = repair(path, ["very", "spitit", "nobody"])
    try:
        assert sorted(report["dropped"]) == ["spitit", "very"]
        assert report["missing"] == ["nobody"]
        data = json.loads(open(path, encoding="utf-8").read())
        assert list(data["users"]) == ["spirit"]
        assert data["users"]["spirit"]["facts"] == {"name": "spirit"}
        assert data["users"]["spirit"]["state"] == {"_last_interaction_id": "b"}
        assert data["last_user"] is None
    finally:
        os.remove(report["backup"])


# ------------------------------------------------------------ interaction ids

def test_interaction_id_is_stored_in_state_not_facts(path):
    planner = offline_planner(path)
    planner.set_user("Sam")

    class FakeInteraction:
        id = "v1_abc"
        steps = []
        output_text = "hi"

    class FakeClient:
        class interactions:
            calls = []

            @classmethod
            def create(cls, **kwargs):
                cls.calls.append(kwargs)
                return FakeInteraction()

    planner.client = FakeClient
    planner.uses_fallback = False
    planner._respond_direct("hello")
    planner._respond_direct("again")
    assert "_last_interaction_id" not in planner.current_user.facts
    assert planner.current_user.get_state("_last_interaction_id") == "v1_abc"
    assert FakeClient.interactions.calls[-1]["previous_interaction_id"] == "v1_abc"
    assert "_last_interaction_id" not in planner.current_user.facts_as_text()


# ------------------------------------------------------------ reset

def test_reset_clears_conversation_state_but_keeps_facts(path):
    from aegis_bridge import AegisBridge

    planner = offline_planner(path)
    planner.set_user("Sam")
    planner.current_user.remember_fact("project", "rover")
    planner.current_user.set_state("_last_interaction_id", "v1_old")
    planner.respond("I've been trying for hours and nothing works!")
    planner.respond("my name is Parth")  # leaves a pending name
    assert planner.emotion_engine.history("user:sam")
    assert planner.pending_name == "Parth"

    bridge = AegisBridge(planner)
    bridge.reset()

    user = planner.current_user
    assert user.key == "sam"
    assert user.turns == []
    assert user.get_state("_last_interaction_id") is None
    assert user.facts["project"] == "rover"
    assert planner.pending_name is None
    assert planner.emotion_engine.history("user:sam") == []
    assert planner.emotion_engine.history("aegis") == []
    assert planner.event_bus.events() == []
    assert planner.last_emotion == "neutral"
    assert bridge.reply == "Aegis conversation reset."


# ------------------------------------------------------------ locking

def test_concurrent_respond_calls_are_serialized(path):
    planner = offline_planner(path)
    active = []
    overlaps = []
    original = planner._observe_emotion

    def slow_observe(text):
        active.append(text)
        if len(active) > 1:
            overlaps.append(list(active))
        time.sleep(0.05)
        original(text)
        active.remove(text)

    planner._observe_emotion = slow_observe
    threads = [threading.Thread(target=planner.respond, args=(f"msg {i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlaps == []
    assert len(planner.current_user.turns) == 10


# ------------------------------------------------------------ trust reporting

def test_bridge_trust_panel_matches_trust_module(path):
    from aegis_bridge import AegisBridge

    planner = offline_planner(path)
    bridge = AegisBridge(planner)
    for _ in range(12):
        planner.current_user.add_turn("user", "hi")
    guest = bridge.trust_state()
    assert guest["trust_level"] == "LOW"
    assert guest["points_breakdown"]["known_relationship"] == 0
    assert guest["relationship_confirmed"] is False
    assert guest["scaled_score"] <= 29        # never looks medium/high while LOW
    assert bridge.state()["trust"] == "LOW"
    assert bridge.state()["trust_score"] == guest["scaled_score"]

    planner.set_user("Sam")
    named = bridge.trust_state()
    t = trust_breakdown(planner.current_user)
    assert named["trust_level"] == t.level.upper() == "MEDIUM"
    assert named["score"] == t.score == 3
    assert named["scaled_score"] == t.percent == 43
    assert "guardrails" not in named and "policy" not in named


def test_negative_score_is_not_floored(path):
    planner = offline_planner(path)
    planner.set_user("Sam")
    planner.current_user.set_state("_correction_count", 5)
    t = trust_breakdown(planner.current_user)
    assert t.score == -2 and t.percent == 0 and t.level == "low"


def test_status_endpoints_have_no_hardcoded_health_claims(path):
    from aegis_bridge import AegisBridge

    planner = offline_planner(path)
    bridge = AegisBridge(planner)
    analytics = bridge.analytics_state()
    assert "status" not in analytics and "memory_footprint" not in analytics
    integrations = bridge.integrations_state()
    assert integrations["gemini_api"]["status"] == "NOT CONFIGURED"
    assert integrations["godot_frontend"]["status"] == "NOT SEEN"
    assert integrations["speech_tts"]["status"] in {"UNTESTED", "UNAVAILABLE", "OK", "ERROR"}
    assert "status" not in bridge.memory_state()
