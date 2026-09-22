from abc import ABC, abstractmethod
from typing import Optional


class GenerationBackend(ABC):
    @abstractmethod
    def generate(self, prompt: str, lang: str, context: dict) -> str:
        """
        prompt: the user's message (already in its original language)
        lang: ISO 639-1 code from LanguageDetector
        context: whatever the caller wants to pass through — memory,
                 emotion state, trust, etc. Backend is free to ignore
                 fields it doesn't use.
        """
        


class GeminiBackend(GenerationBackend):
    """
    Wraps the existing Gemini call. Adds one instruction to the system
    prompt: respond in `lang`. Everything else (mind.as_prompt, memory,
    emotion, trust) stays exactly as it is today in mind.py.
    """

    def __init__(self, gemini_client):
        self.client = gemini_client

    def generate(self, prompt: str, lang: str, context: dict) -> str:
        if lang == "hi":
            lang_instruction = (
                "\nLanguage policy: reply in pure Hindi, not Hinglish. "
                "Do not mix Hindi and English words unless the user explicitly asks for a switch or code-mixing. "
                "Keep the sentence structure, vocabulary, and tone natural for Hindi."
            )
        else:
            lang_instruction = (
                "\nRespond in language code: {lang}. Match the user's language unless they ask you to switch."
            ).format(lang=lang)

        system_prompt = context.get("system_prompt", "") + lang_instruction
        # Delegate to whatever mind.py already does to call Gemini.
        # Left as an integration point rather than reimplemented here,
        # since the real call lives in mind.py / planner.py.
        return self.client.generate(
            prompt=prompt,
            lang=lang,
            context={**context, "system_prompt": system_prompt},
        )


class LocalModelBackend(GenerationBackend):
    """
    Stub for a self-hosted multilingual model (e.g. NLLB for
    translation-pivot, or a small multilingual instruct model like Gemma).

    Not implemented here on purpose — this is real infra work (hosting,
    quantization, latency tuning) that shouldn't be built speculatively.
    Build this only when there's an actual offline requirement driving it.
    """

    def __init__(self, model_path: str):
        raise NotImplementedError(
            "LocalModelBackend is a framework extension point, not yet implemented. "
            "Build this when a real offline/no-API requirement exists — see "
            "language/README.md for the interface contract."
        )

    def generate(self, prompt: str, lang: str, context: dict) -> str:
        raise NotImplementedError


class ScriptedBackend(GenerationBackend):
    """
    Zero-generation backend: looks up a canned response from scripts/.
    True LLM-free path — this is what "least LLM dependence" actually
    buys you for free, without hosting a second model.
    """

    def __init__(self, script_store: dict):
        # script_store: {"en": {"greeting": "Hello!"}, "hi": {"greeting": "नमस्ते!"}}
        self.scripts = script_store

    def generate(self, prompt: str, lang: str, context: dict) -> str:
        intent = context.get("intent")
        lang_scripts = self.scripts.get(lang) or self.scripts.get("en", {})
        return lang_scripts.get(intent, lang_scripts.get("fallback", ""))
