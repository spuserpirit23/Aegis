from collections.abc import Iterable

from .backend import GenerationBackend, GenerationRequest, GenerationResult


class GenerationRouter:
    def __init__(self, backends: Iterable[GenerationBackend]):
        self.backends = list(backends)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        for backend in self.backends:
            result = backend.generate(request)
            if result.handled:
                return result
        return GenerationResult(
            backend="none",
            metadata={"reason": "No backend handled the request"},
        )