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

# Outcome of the most recent speak() call, reported by the bridge instead of
# a hardcoded "ONLINE".
_last_status = {"status": "UNTESTED", "detail": "no speech attempted yet"}
if pyttsx3 is None:
    _last_status = {"status": "UNAVAILABLE", "detail": "pyttsx3 is not installed"}


def tts_status() -> dict:
    return dict(_last_status)


def _record(status: str, detail: str) -> None:
    global _last_status
    _last_status = {"status": status, "detail": detail}


def _create_engine():
    if pyttsx3 is None:
        return None

    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        return engine
    except Exception as exc:  # pragma: no cover - depends on OS/driver availability
        print(f"speech output unavailable: {exc}")
        _record("UNAVAILABLE", f"engine init failed: {exc}")
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
        _record("OK", "last utterance spoken")
    except Exception as exc:  # pragma: no cover - depends on OS/driver availability
        print(f"speech output failed: {exc}")
        _record("ERROR", f"speech output failed: {exc}")
    finally:
        try:
            engine.stop()
        except Exception:
            pass
