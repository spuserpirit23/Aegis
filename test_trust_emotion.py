"""
Unit tests for brain/trust.py and brain/emotion.py.

Run: python3 test_trust_emotion.py

These are pure-function tests — no Gemini calls, no real memory.json,
no network. Fast enough to run before every demo rehearsal. If any of
these fail, do not run the live demo until they pass; a wrong trust
score is a wrong premise for the whole reply.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from brain.trust import compute_trust_score, trust_level
from brain.emotion import compute_emotion


class FakeMemory:
    """Minimal stand-in for memory.Memory — just the attributes
    trust.py/emotion.py actually read (turns, facts)."""

    def __init__(self, turns=None, facts=None):
        self.turns = turns or []
        self.facts = facts or {}


def user_turn(text):
    return {"role": "user", "content": text}


def assistant_turn(text):
    return {"role": "assistant", "content": text}


# ---------------------------------------------------------------- trust

def test_fresh_memory_is_low_trust():
    mem = FakeMemory()
    assert trust_level(mem) == "low", f"expected low, got {trust_level(mem)}"


def test_known_relationship_raises_trust():
    mem_stranger = FakeMemory()
    mem_known = FakeMemory(facts={"nickname": "Spirit"})
    assert compute_trust_score(mem_known) > compute_trust_score(mem_stranger)


def test_more_turns_raises_trust_up_to_cap():
    few = FakeMemory(turns=[user_turn("hi")] * 2)
    many = FakeMemory(turns=[user_turn("hi")] * 30)
    assert compute_trust_score(many) > compute_trust_score(few)


def test_trust_score_is_capped_not_unbounded():
    absurd = FakeMemory(turns=[user_turn("hi")] * 5000, facts={"nickname": "Spirit"})
    # cap = relationship(3) + interaction cap(4) = 7, corrections=0
    assert compute_trust_score(absurd) == 7, compute_trust_score(absurd)


def test_real_memory_shape_scores_high():
    # Mirrors your actual memory.json: 18 turns + nickname/name known.
    mem = FakeMemory(
        turns=[user_turn("hi")] * 18,
        facts={"nickname": "Spirit", "name": "Arpan"},
    )
    assert trust_level(mem) == "high", trust_level(mem)


def test_corrections_lower_trust_if_tracked():
    base = FakeMemory(facts={"nickname": "Spirit"})
    corrected = FakeMemory(facts={"nickname": "Spirit", "_correction_count": 5})
    assert compute_trust_score(corrected) < compute_trust_score(base)


def test_malformed_correction_count_does_not_crash():
    mem = FakeMemory(facts={"_correction_count": "not_a_number"})
    trust_level(mem)  # should not raise


# -------------------------------------------------------------- emotion

def test_no_turns_is_neutral():
    mem = FakeMemory()
    assert compute_emotion(mem, "medium") == "neutral"


def test_negative_word_is_concerned():
    mem = FakeMemory(turns=[user_turn("this is broken, getting an error")])
    assert compute_emotion(mem, "medium") == "concerned"


def test_positive_word_is_happy_at_medium_or_high_trust():
    mem = FakeMemory(turns=[user_turn("awesome, it works now")])
    assert compute_emotion(mem, "medium") == "happy"
    assert compute_emotion(mem, "high") == "happy"


def test_positive_word_stays_neutral_at_low_trust():
    # Deliberate: don't perform warmth toward someone not yet trusted,
    # even if their message reads positive.
    mem = FakeMemory(turns=[user_turn("awesome, it works now")])
    assert compute_emotion(mem, "low") == "neutral"


def test_uncertain_word_is_thinking():
    mem = FakeMemory(turns=[user_turn("hmm not sure about this")])
    assert compute_emotion(mem, "medium") == "thinking"


def test_only_reads_most_recent_user_turn():
    mem = FakeMemory(turns=[
        user_turn("this is broken"),
        assistant_turn("let's debug it"),
        user_turn("awesome, fixed it"),
    ])
    assert compute_emotion(mem, "medium") == "happy"


def test_negative_beats_positive_when_both_present():
    mem = FakeMemory(turns=[user_turn("thanks but this is still broken")])
    assert compute_emotion(mem, "medium") == "concerned"


ALL_TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]


def run():
    passed, failed = 0, []
    for test in ALL_TESTS:
        try:
            test()
            passed += 1
            print(f"  ok   {test.__name__}")
        except AssertionError as e:
            failed.append((test.__name__, str(e)))
            print(f"  FAIL {test.__name__}: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")

    print(f"\n{passed}/{len(ALL_TESTS)} passed")
    if failed:
        print("\nFailed:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    run()
