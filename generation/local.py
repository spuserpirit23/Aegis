from typing import Any, Protocol

from .backend import GenerationBackend, GenerationRequest, GenerationResult


class LocalModelProvider(Protocol):
    def generate(self, request: GenerationRequest) -> Any:
        ...


class LocalModelBackend(GenerationBackend):
    """Provider-neutral adapter for an optional local inference engine."""

    name = "local"

    def __init__(self, provider: LocalModelProvider | None = None):
        self.provider = provider

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if self.provider is None:
            return GenerationResult(backend=self.name)
        return _provider_result(self.provider.generate(request), self.name)


def _provider_result(value: Any, backend: str) -> GenerationResult:
    if isinstance(value, GenerationResult):
        return value
    if isinstance(value, str) and value:
        return GenerationResult(text=value, backend=backend, handled=True)
    return GenerationResult(backend=backend)