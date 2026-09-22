
import os
from typing import Optional

import yaml

try:
    from .detector import LanguageDetector
    from .backend import GeminiBackend, ScriptedBackend
    from .router import LanguageRouter, RouteResult
except ImportError:
    from detector import LanguageDetector
    from backend import GeminiBackend, ScriptedBackend
    from router import LanguageRouter, RouteResult

_HERE = os.path.dirname(__file__)


class LanguageEngine:
    def __init__(
        self,
        gemini_client=None,
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
        scripted = ScriptedBackend(script_store) if script_store else None
        llm = GeminiBackend(gemini_client) if gemini_client is not None else None

        self.router = LanguageRouter(
            detector=detector,
            scripted_backend=scripted,
            llm_backend=llm,
            known_intents=set(self.config.get("known_intents", [])),
        )

    def reply(
        self,
        text: str,
        intent: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> RouteResult:
        return self.router.route(text, intent, context or {})
