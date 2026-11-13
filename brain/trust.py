"""
Trust: derives a trust level from Memory state, in code, in ~1ms.

Not a separate LLM reasoning stage — that's the thing we deliberately
cut. This is a small scoring function over signals already sitting in
memory.json, called once per turn before the (single) Gemini call.

Levels: "low" | "medium" | "high"

Every signal is exposed through :func:`trust_breakdown`, which is what the
UI shows, so the panel can never disagree with the level actually used.
Identity strength gates the ceiling: a guest is always LOW, a self-declared
("claimed") identity is capped at MEDIUM however long the history, and only
a verified identity (brain/identity.py) can reach HIGH. Saying a name must
never be enough to earn the highest trust.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from brain.identity import AuthStatus, coerce_status
from brain.memory import GUEST_KEY


# Signals and their point values. Tune these, don't rewrite the shape,
# unless a new signal is genuinely needed.
_KNOWN_RELATIONSHIP_POINTS = 3   # a confirmed (self-declared) name/nickname
_INTERACTION_POINTS_CAP = 4      # up to this many points from turn count
_INTERACTION_POINTS_PER_TURNS = 5  # +1 point per this many turns, capped above
_CORRECTION_PENALTY = -1         # user had to correct Aegis

_LOW_MAX = 2      # score <= this -> low
_MEDIUM_MAX = 5    # score <= this -> medium; above -> high

MAX_SCORE = _KNOWN_RELATIONSHIP_POINTS + _INTERACTION_POINTS_CAP


@dataclass(frozen=True)
class TrustBreakdown:
    is_guest: bool
    auth_status: str
    identity_confirmed: bool
    turn_count: int
    corrections: int
    known_relationship: int
    interaction_history: int
    correction_penalty: int
    score: int
    level: str
    percent: int          # score mapped onto 0..100 of MAX_SCORE; no floor
    reason: str
    capped: bool = False  # level held below what the score alone would give

    def to_dict(self) -> dict:
        return asdict(self)


def _is_guest(memory) -> bool:
    return getattr(memory, "key", None) == GUEST_KEY


def _auth_status(memory) -> AuthStatus:
    if _is_guest(memory):
        return AuthStatus.GUEST
    # Objects without an explicit status (older callers, test doubles) are
    # treated as self-declared: never guest-shared, never above MEDIUM.
    status = coerce_status(getattr(memory, "auth_status", AuthStatus.CLAIMED))
    return AuthStatus.CLAIMED if status is AuthStatus.GUEST else status


def _corrections(memory) -> int:
    state = getattr(memory, "state", None) or {}
    raw = state.get("_correction_count", memory.facts.get("_correction_count", 0))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _level_for(score: int) -> str:
    if score <= _LOW_MAX:
        return "low"
    if score <= _MEDIUM_MAX:
        return "medium"
    return "high"


def trust_breakdown(memory) -> TrustBreakdown:
    """Pure function of memory state; safe to call every turn and from the UI."""
    guest = _is_guest(memory)
    identity = not guest and bool(memory.facts.get("nickname") or memory.facts.get("name"))
    turn_count = len(memory.turns)
    corrections = _corrections(memory)

    known = _KNOWN_RELATIONSHIP_POINTS if identity else 0
    interaction = min(turn_count // _INTERACTION_POINTS_PER_TURNS, _INTERACTION_POINTS_CAP)
    penalty = _CORRECTION_PENALTY * corrections
    score = known + interaction + penalty

    status = _auth_status(memory)
    score_level = _level_for(score)
    capped = False
    if guest:
        # The guest bucket is shared by every unidentified speaker, so its
        # turn count says nothing about any one person: always low.
        level = "low"
        capped = score_level != "low"
        reason = "Unidentified guest: trust is fixed at LOW until a name is confirmed."
    elif status is not AuthStatus.VERIFIED and score_level == "high":
        level = "medium"
        capped = True
        reason = (f"Score {score} would be HIGH, but the identity is self-declared, not "
                  f"verified: capped at MEDIUM.")
    else:
        level = score_level
        reason = (f"Score {score} ({'<=' if level != 'high' else '>'} "
                  f"{_LOW_MAX if level == 'low' else _MEDIUM_MAX}) -> {level.upper()}.")

    percent = 0 if MAX_SCORE <= 0 else round(100 * max(0, min(score, MAX_SCORE)) / MAX_SCORE)
    # Never let the gauge look higher than the level actually applied.
    if level == "low":
        percent = min(percent, round(100 * _LOW_MAX / MAX_SCORE))
    elif level == "medium":
        percent = min(percent, round(100 * _MEDIUM_MAX / MAX_SCORE))
    return TrustBreakdown(
        is_guest=guest,
        auth_status=status.value,
        identity_confirmed=identity,
        turn_count=turn_count,
        corrections=corrections,
        known_relationship=known,
        interaction_history=interaction,
        correction_penalty=penalty,
        score=score,
        level=level,
        percent=percent,
        reason=reason,
        capped=capped,
    )


def compute_trust_score(memory) -> int:
    return trust_breakdown(memory).score


def trust_level(memory) -> str:
    return trust_breakdown(memory).level
