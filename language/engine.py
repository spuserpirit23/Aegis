
import os
from typing import Optional

import yaml

try:
    from .detector import LanguageDetector
    from generation import (
        CloudLLMBackend,
        GenerationBackend,
        GenerationRouter,
        ScriptedBackend,
    )
    from .router import LanguageRouter, RouteResult
except ImportError:
    from detector import LanguageDetector
    from generation import (
        CloudLLMBackend,
        GenerationBackend,
        GenerationRouter,
        ScriptedBackend,
    )
    from router import LanguageRouter, RouteResult

_HERE = os.path.dirname(__file__)


class LanguageEngine:
    def __init__(
        self,
        generation: Optional[GenerationRouter] = None,
        cloud_provider=None,
        config_path: Optional[str] = None,
        scripts_dir: Optional[str] = None,
    ):
        config_path = config_path or os.path.join(_HERE, "config.yaml")
        scripts_dir = scripts_dir or _HERE

        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        script_store = {}
        for lang in self.config.get("supported_languages", []):
            path = os.path.join(scripts_dir, f"{lang}.yaml")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    script_store[lang] = yaml.safe_load(f)

        detector = LanguageDetector(
            default_lang=self.config.get("default_language", "en"),
            min_confidence=self.config.get("min_detection_confidence", 0.5),
        )
        if generation is None:
            backends: list[GenerationBackend] = [ScriptedBackend(script_store)]
            if cloud_provider is not None:
                backends.append(CloudLLMBackend(cloud_provider))
            generation = GenerationRouter(backends)

        self.router = LanguageRouter(
            detector=detector,
            scripted_backend=generation.backends[0] if generation.backends else None,
            llm_backend=generation.backends[-1] if len(generation.backends) > 1 else None,
            known_intents=set(self.config.get("known_intents", [])),
        )
        self.router.generation = generation

    def reply(
        self,
        text: str,
        intent: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> RouteResult:
        return self.router.route(text, intent, context or {})
