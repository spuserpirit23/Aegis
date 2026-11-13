"""Regression: the shared guest bucket must always be low trust.

Memory.get_user() seeds every bucket with facts["name"], which used to earn
the guest bucket +3 "known relationship" points (medium trust on turn one).
"""
import os
import tempfile
from unittest.mock import patch

import pytest

from brain.memory import GUEST_KEY, Memory
from brain.trust import compute_trust_score, trust_level


@pytest.fixture
def memory():
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        path = handle.name
    yield Memory(path=path)
    if os.path.exists(path):
        os.remove(path)


def test_fresh_guest_bucket_is_low_and_scores_zero(memory):
    guest = memory.get_user(GUEST_KEY)
    assert "name" not in guest.facts  # guest is no longer seeded with a name fact
    assert compute_trust_score(guest) == 0
    assert trust_level(guest) == "low"


def test_guest_stays_low_however_many_turns(memory):
    guest = memory.get_user("Guest")  # bridge spelling, same bucket
    for _ in range(60):
        guest.add_turn("user", "hi")
    assert compute_trust_score(guest) == 4  # interaction points still visible on screen
    assert trust_level(guest) == "low"


def test_named_users_score_but_claimed_identity_caps_at_medium(memory):
    sam = memory.get_user("Sam")
    assert compute_trust_score(sam) == 3
    assert trust_level(sam) == "medium"
    for _ in range(30):
        sam.add_turn("user", "hi")
    assert compute_trust_score(sam) == 7
    assert trust_level(sam) == "medium"  # self-declared: never HIGH
    verified = memory.get_user("Sam", auth_status="verified", verified_by="pin")
    assert trust_level(verified) == "high"


def test_planner_guest_gets_low_trust_and_no_warmth(memory):
    from brain.planner import Planner

    with patch("brain.planner.load_env_file", return_value={}), \
            patch.dict(os.environ, {"AEGIS_EMOTION_PROVIDER": "rule"}):
        os.environ.pop("GEMINI_API_KEY", None)
        planner = Planner(memory)
    planner.respond("thanks, that worked great")

    assert planner.current_user.key == GUEST_KEY
    assert planner.emotion_context.trust == "low"
    assert planner.emotion_context.aegis.tendency == "happy"  # internal state unchanged
    assert planner.last_emotion != "happy"                    # warmth not performed


def test_bridge_reports_low_trust_for_guest(memory):
    from aegis_bridge import AegisBridge
    from brain.planner import Planner

    with patch("brain.planner.load_env_file", return_value={}):
        os.environ.pop("GEMINI_API_KEY", None)
        planner = Planner(memory)
    planner.respond("hello there")
    assert AegisBridge(planner).trust_state()["trust_level"] == "LOW"
