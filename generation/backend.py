from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenerationRequest:
    text: str
    language: str | None = None
    context: dict[str, Any] | None = None


@dataclass
class GenerationResult:
    text: str = ""
    backend: str = ""
    handled: bool = False
    metadata: dict[str, Any] | None = field(default_factory=dict)


class GenerationBackend(ABC):
    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate a response, or explicitly decline the request."""