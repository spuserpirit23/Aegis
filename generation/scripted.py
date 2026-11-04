from typing import Any

from .backend import GenerationBackend, GenerationRequest, GenerationResult


class ScriptedBackend(GenerationBackend):
    """Return configured deterministic responses for recognized intents."""

    name = "scripted"

    def __init__(self, scripts: dict[str, dict[str, str]] | None = None):
        self.scripts = scripts or {}

    def generate(self, request: GenerationRequest) -> GenerationResult:
        context: dict[str, Any] = request.context or {}
        intent = context.get("intent")
        if not intent:
            return GenerationResult(backend=self.name)

        language_scripts = self.scripts.get(request.language or "")
        if language_scripts is None:
            language_scripts = self.scripts.get("en", {})
        response = language_scripts.get(intent, "")
        if not response:
            return GenerationResult(backend=self.name)
        return GenerationResult(text=response, backend=self.name, handled=True)