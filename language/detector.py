"""
Language detection — always local, always cheap.

Uses fasttext's lid.176 model if available (more accurate, works on short
text), falls back to langdetect (pure Python, no model download) if
fasttext isn't installed. Either way: zero LLM calls, zero API cost.

This mirrors the existing MindOS pattern (trust.py, emotion.py,
permission.py): deterministic, code-based, unit-testable.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class LanguageResult:
    code: str          # ISO 639-1, e.g. "en", "hi"
    confidence: float  # 0.0-1.0
    raw_backend: str   # which detector produced this, for debugging


class LanguageDetector:
    """
    Detects the language of a text string. Falls back gracefully if the
    preferred backend isn't installed, and falls back again to a default
    language if detection confidence is too low to trust (short strings,
    emoji-only input, etc. are common failure cases).
    """

    def __init__(self, default_lang: str = "en", min_confidence: float = 0.5):
        self.default_lang = default_lang
        self.min_confidence = min_confidence
        self._backend = self._load_backend()

    def _load_backend(self):
        try:
            import fasttext  # type: ignore
            import os

            model_path = os.environ.get("FASTTEXT_LID_MODEL", "lid.176.ftz")
            if os.path.exists(model_path):
                model = fasttext.load_model(model_path)
                return ("fasttext", model)
        except ImportError:
            pass

        try:
            import langdetect  # type: ignore
            from langdetect import DetectorFactory

            DetectorFactory.seed = 0  # deterministic results
            return ("langdetect", langdetect)
        except ImportError:
            pass

        return ("none", None)

    def detect(self, text: str) -> LanguageResult:
        text = (text or "").strip()
        if not text:
            return LanguageResult(self.default_lang, 0.0, "empty")

        backend_name, backend = self._backend

        if backend_name == "fasttext":
            text_clean = text.replace("\n", " ")
            labels, probs = backend.predict(text_clean, k=1)
            code = labels[0].replace("__label__", "")
            confidence = float(probs[0])
            if confidence < self.min_confidence:
                return LanguageResult(self.default_lang, confidence, "fasttext_low_conf")
            return LanguageResult(code, confidence, "fasttext")

        if backend_name == "langdetect":
            try:
                langs = backend.detect_langs(text)
                top = langs[0]
                if top.prob < self.min_confidence:
                    return LanguageResult(self.default_lang, top.prob, "langdetect_low_conf")
                return LanguageResult(top.lang, top.prob, "langdetect")
            except Exception:
                return LanguageResult(self.default_lang, 0.0, "langdetect_error")

        # No backend installed — degrade to default rather than crash.
        return LanguageResult(self.default_lang, 0.0, "no_backend")
