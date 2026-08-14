"""
Trust: derives a trust level from Memory state, in code, in ~1ms.

Not a separate LLM reasoning stage — that's the thing we deliberately
cut. This is a small scoring function over signals already sitting in
memory.json, called once per turn before the (single) Gemini call.

Levels: "low" | "medium" | "high"

Scoring is intentionally simple and legible — every signal here is
something you can point to on screen during a demo and say "this is
why trust is at this level," which matters more than sophistication
for an investor demo.
"""

from __future__ import annotations


# Signals and their point values. Tune these, don't rewrite the shape,
# unless a new signal is genuinely needed.
_KNOWN_RELATIONSHIP_POINTS = 3   # builder identity confirmed (nickname/name in facts)
_INTERACTION_POINTS_CAP = 4      # up to this many points from turn count
_INTERACTION_POINTS_PER_TURNS = 5  # +1 point per this many turns, capped above
_CORRECTION_PENALTY = -1         # user had to correct Aegis (see note below)

_LOW_MAX = 2      # score <= this -> low
_MEDIUM_MAX = 5    # score <= this -> medium; above -> high


def compute_trust_score(memory) -> int:
    """Pure function of memory state. No side effects, no I/O beyond
    what memory already loaded — safe to call every turn."""
    score = 0

    if memory.facts.get("nickname") or memory.facts.get("name"):
        score += _KNOWN_RELATIONSHIP_POINTS

    turn_count = len(memory.turns)
    score += min(turn_count // _INTERACTION_POINTS_PER_TURNS, _INTERACTION_POINTS_CAP)

    # Explicit correction counter, if planner/memory starts tracking it
    # (see memory.py note in Week 2 — not wired yet, safe no-op today).
    corrections = memory.facts.get("_correction_count", 0)
    try:
        score += _CORRECTION_PENALTY * int(corrections)
    except (TypeError, ValueError):
        pass

    return score


def trust_level(memory) -> str:
    score = compute_trust_score(memory)
    if score <= _LOW_MAX:
        return "low"
    if score <= _MEDIUM_MAX:
        return "medium"
    return "high"
