
from dataclasses import dataclass
from typing import Optional

try:
    from .detector import LanguageDetector, LanguageResult
    from .backend import GenerationBackend, ScriptedBackend
except ImportError:
    from detector import LanguageDetector, LanguageResult
    from backend import GenerationBackend, ScriptedBackend


@dataclass
class RouteResult:
    text: str
    lang: str
    source: str  # "scripted" | "llm" | "fallback"


class LanguageRouter:
    def __init__(
        self,
        detector: LanguageDetector,
        scripted_backend: Optional[ScriptedBackend],
        llm_backend: Optional[GenerationBackend],
        known_intents: Optional[set] = None,
    ):
        self.detector = detector
        self.scripted = scripted_backend
        self.llm = llm_backend
        # Intents scripts/ actually has coverage for. Keep this explicit
        # rather than trying every intent against scripts and hoping —
        # a silent bad scripted match is worse than a clean LLM fallback.
        self.known_intents = known_intents or set()

    def route(self, text: str, intent: Optional[str], context: dict) -> RouteResult:
        detected: LanguageResult = self.detector.detect(text)
        lang = detected.code

        if intent in self.known_intents and self.scripted is not None:
            reply = self.scripted.generate(text, lang, {**context, "intent": intent})
            if reply:
                return RouteResult(reply, lang, "scripted")
            # Scripted lookup missed (e.g. no translation for this lang
            # yet) — fall through rather than return empty string.

        if self.llm is not None:
            reply = self.llm.generate(text, lang, context)
            return RouteResult(reply, lang, "llm")

        return RouteResult("", lang, "fallback")
