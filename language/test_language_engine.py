
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from language.detector import LanguageDetector
from language.backend import ScriptedBackend, GeminiBackend
from language.router import LanguageRouter
from brain.memory import Memory
from brain.planner import Planner


def test_detector_defaults_without_backend_installed():
    d = LanguageDetector(default_lang="en")
    result = d.detect("")
    assert result.code == "en"
    assert result.raw_backend == "empty"
    print("OK: empty string -> default lang")


def test_detector_basic():
    d = LanguageDetector(default_lang="en")
    result = d.detect("Hello, how are you today?")
    print(f"detected: {result.code} (conf={result.confidence:.2f}, backend={result.raw_backend})")


def test_scripted_backend_known_intent():
    scripts = {
        "en": {"greeting": "Hello!", "fallback": "?"},
        "hi": {"greeting": "नमस्ते!", "fallback": "?"},
    }
    backend = ScriptedBackend(scripts)
    assert backend.generate("hi", "en", {"intent": "greeting"}) == "Hello!"
    assert backend.generate("hi", "hi", {"intent": "greeting"}) == "नमस्ते!"
    print("OK: scripted backend returns correct language for known intent")


def test_router_uses_scripted_for_known_intent_no_llm_called():
    scripts = {"en": {"greeting": "Hello!", "fallback": "?"}}
    scripted = ScriptedBackend(scripts)

    class ExplodingLLM:
        def generate(self, *a, **kw):
            raise AssertionError("LLM backend should NOT be called for a known intent")

    d = LanguageDetector(default_lang="en")
    router = LanguageRouter(d, scripted, ExplodingLLM(), known_intents={"greeting"})
    result = router.route("hi", "greeting", {})
    assert result.source == "scripted"
    assert result.text == "Hello!"
    print("OK: router short-circuits to scripted path, never touches LLM")


def test_router_falls_through_to_llm_for_unknown_intent():
    scripts = {"en": {"greeting": "Hello!", "fallback": "?"}}
    scripted = ScriptedBackend(scripts)

    class StubLLM:
        def generate(self, prompt, lang, context):
            return f"llm-reply[{lang}]"

    d = LanguageDetector(default_lang="en")
    router = LanguageRouter(d, scripted, StubLLM(), known_intents={"greeting"})
    result = router.route("what's the weather doing near the ridge tomorrow", None, {})
    assert result.source == "llm"
    print("OK: unknown intent falls through to LLM backend")


def test_gemini_backend_instructs_hindi_without_hinglish():
    class StubClient:
        def generate(self, prompt, lang, context):
            return {"prompt": prompt, "lang": lang, "context": context}

    backend = GeminiBackend(StubClient())
    result = backend.generate("नमस्ते, आप कैसे हैं?", "hi", {"system_prompt": "base instruction"})

    prompt = result["context"]["system_prompt"].lower()
    assert "pure hindi" in prompt
    assert "hinglish" in prompt
    assert "do not" in prompt
    print("OK: Gemini backend explicitly blocks Hinglish on Hindi prompts")


def test_live_hindi_gemini_reply():
    if os.environ.get("AEGIS_LIVE_GEMINI_TEST") != "1":
        print("SKIP: set AEGIS_LIVE_GEMINI_TEST=1 to run the live Gemini test")
        return

    with tempfile.NamedTemporaryFile(delete=False) as handle:
        path = handle.name
    try:
        planner = Planner(Memory(path=path))
        assert not planner.uses_fallback, "GEMINI_API_KEY is required for the live test"
        text = "नमस्ते, आज आप कैसे हैं?"
        planner._resolve_speaker(text)
        planner.current_user.add_turn("user", text)
        result = planner.language_engine.reply(
            text=text,
            intent=None,
            context={"system_prompt": planner._build_system_instruction()},
        )
        assert result.source == "llm"
        assert result.lang == "hi"
        assert result.text
        assert re.search(r"[\u0900-\u097f]", result.text)
        print(f"OK: live Gemini Hindi reply -> {result.text}")
    finally:
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    test_detector_defaults_without_backend_installed()
    test_detector_basic()
    test_scripted_backend_known_intent()
    test_router_uses_scripted_for_known_intent_no_llm_called()
    test_router_falls_through_to_llm_for_unknown_intent()
    test_live_hindi_gemini_reply()
    print("\nAll language engine tests passed.")
