
from dataclasses import dataclass
from typing import Any, Optional

try:
    from .detector import LanguageDetector, LanguageResult
    from .backend import GenerationBackend, ScriptedBackend
    from generation import (
        GenerationBackend as CoreGenerationBackend,
        GenerationRequest,
        GenerationResult,
        GenerationRouter,
    )
except ImportError:
    from detector import LanguageDetector, LanguageResult
    from backend import GenerationBackend, ScriptedBackend
    from generation import (
        GenerationBackend as CoreGenerationBackend,
        GenerationRequest,
        GenerationResult,
        GenerationRouter,
    )


@dataclass
class RouteResult:
    text: str
    lang: str
    source: str


class _LegacyBackendAdapter(CoreGenerationBackend):
    def __init__(self, backend, name: str):
        self.backend = backend
        self.name = name

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if self.name == "scripted" and not (request.context or {}).get("intent"):
            return GenerationResult(backend=self.name)
        value = self.backend.generate(
            request.text,
            request.language or "",
            request.context or {},
        )
        if isinstance(value, GenerationResult):
            return value
        if isinstance(value, str) and value:
            return GenerationResult(text=value, backend=self.name, handled=True)
        return GenerationResult(backend=self.name)


class LanguageRouter:
    def __init__(
        self,
        detector: LanguageDetector,
        scripted_backend: Optional[Any],
        llm_backend: Optional[Any],
        known_intents: Optional[set] = None,
    ):
        self.detector = detector
        self.scripted = scripted_backend
        self.llm = llm_backend
        self.known_intents = known_intents or set()
        backends = []
        for backend, name in (
            (scripted_backend, "scripted"),
            (llm_backend, "cloud"),
        ):
            if backend is None:
                continue
            if isinstance(backend, CoreGenerationBackend):
                backends.append(backend)
            else:
                backends.append(_LegacyBackendAdapter(backend, name))
        self.generation = GenerationRouter(backends)

    def route(self, text: str, intent: Optional[str], context: dict) -> RouteResult:
        detected: LanguageResult = self.detector.detect(text)
        lang = detected.code

        request = GenerationRequest(
            text=text,
            language=lang,
            context={**context, "intent": intent} if intent in self.known_intents else context,
        )
        result: GenerationResult = self.generation.generate(request)
        source = "llm" if result.backend == "cloud" else result.backend
        if not result.handled:
            source = "fallback"
        return RouteResult(result.text, lang, source)
