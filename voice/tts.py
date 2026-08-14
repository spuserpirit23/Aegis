"""
Text-to-speech: spoken replies via Windows' built-in SAPI5 voices.

Uses pyttsx3, which wraps SAPI5 on Windows -- fully offline, no API
key, no network round-trip. This means voice *output* keeps working
even if your internet connection is spotty; only the LLM call and STT
step actually need network access.
"""

try:
    import pyttsx3
except ImportError:  # pragma: no cover - exercised when the dependency is absent
    pyttsx3 = None


def _create_engine():
    if pyttsx3 is None:
        return None

    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        return engine
    except Exception as exc:  # pragma: no cover - depends on OS/driver availability
        print(f"speech output unavailable: {exc}")
        return None


def speak(text: str):
    if not text:
        return

    engine = _create_engine()
    if engine is None:
        print(f"assistant: {text}")
        return

    try:
        engine.say(text)
        engine.runAndWait()
    except Exception as exc:  # pragma: no cover - depends on OS/driver availability
        print(f"speech output failed: {exc}")
    finally:
        try:
            engine.stop()
        except Exception:
            pass
