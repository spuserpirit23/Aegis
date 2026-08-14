
from __future__ import annotations

_POSITIVE_WORDS = {"great", "awesome", "nice", "love", "thanks", "cool", "works", "fixed", "yes"}
_NEGATIVE_WORDS = {"broken", "bug", "error", "fail", "stuck", "wrong", "not working", "frustrat"}
_UNCERTAIN_WORDS = {"maybe", "not sure", "confused", "?", "hmm", "think"}


def _last_user_text(memory) -> str:
    for turn in reversed(memory.turns):
        if turn.get("role") == "user":
            return turn.get("content", "").lower()
    return ""


def compute_emotion(memory, trust: str) -> str:
    text = _last_user_text(memory)

    if any(w in text for w in _NEGATIVE_WORDS):
        return "concerned"
    if any(w in text for w in _POSITIVE_WORDS):
        return "happy" if trust != "low" else "neutral"
    if any(w in text for w in _UNCERTAIN_WORDS):
        return "thinking"
    return "neutral"
