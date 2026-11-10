from typing import Any, Protocol
from .backend import GenerationBackend, GenerationRequest, GenerationResult
from .local import _provider_result


class CloudLLMProvider(Protocol):
    def generate(self, request: GenerationRequest) -> Any:
        ...


class CloudLLMBackend(GenerationBackend):
    """Provider-neutral adapter for an injected cloud generation client."""

    name = "cloud"

    def __init__(self, provider: CloudLLMProvider | None = None):
        self.provider = provider

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if self.provider is None:
            return GenerationResult(backend=self.name, metadata={"reason": "No backend handled the request"})
        return _provider_result(self.provider.generate(request), self.name)