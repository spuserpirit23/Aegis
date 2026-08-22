from __future__ import annotations

from transformers import pipeline


_emotion_classifier = pipeline(
    "text-classification",
    model="j-hartmann/emotion-english-distilroberta-base",
)


def _last_user_text(memory) -> str:
    for turn in reversed(memory.turns):
        if turn.get("role") == "user":
            return turn.get("content", "").lower()
    return ""


def _detect_emotion(text: str) -> tuple[str, float]:
    """Use the pretrained model to detect emotion and confidence."""
    if not text.strip():
        return "neutral", 1.0

    result = _emotion_classifier(text)[0]

    return result["label"], float(result["score"])


def _map_emotion(
    model_emotion: str,
    confidence: float,
    text: str,
) -> str:
    """
    Convert the pretrained model's emotion into the
    emotion vocabulary used by the Emotion Engine.
    """

    text = text.lower()

    # Explicit uncertainty takes priority.
    uncertainty_words = {
        "hmm",
        "maybe",
        "not sure",
        "confused",
        "don't know",
        "do not know",
    }

    if any(word in text for word in uncertainty_words):
        return "thinking"

    # Negative signals take priority over positive signals.
    negative_words = {
        "broken",
        "bug",
        "error",
        "fail",
        "stuck",
        "wrong",
        "not working",
        "frustrat",
    }

    if (
        any(word in text for word in negative_words)
        or model_emotion in {"anger", "sadness", "fear", "disgust"}
    ):
        return "concerned"

    # Positive language → happy.
    positive_words = {
        "awesome",
        "great",
        "nice",
        "love",
        "thanks",
        "cool",
        "works",
        "fixed",
        "yes",
    }

    if any(word in text for word in positive_words):
        return "happy"

    return "neutral"


def compute_emotion(memory, trust: str) -> str:
    """
    Existing framework interface.

    Returns a simple emotion string so the existing
    framework remains compatible.
    """

    text = _last_user_text(memory)

    if not text:
        return "neutral"

    model_emotion, confidence = _detect_emotion(text)

    emotion = _map_emotion(
        model_emotion,
        confidence,
        text,
    )

    # Preserve the existing trust behaviour:
    # low trust should not produce a warm/happy response.
    if trust == "low" and emotion == "happy":
        return "neutral"

    return emotion