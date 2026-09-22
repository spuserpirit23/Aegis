try:
    import speech_recognition as sr
except ImportError:  # pragma: no cover - exercised when the dependency is absent
    sr = None

import threading
from queue import Empty, Queue

_recognizer = sr.Recognizer() if sr is not None else None
_TRANSCRIPTION_TIMEOUT_SECONDS = 50


def microphone_status() -> str:
    if sr is None:
        return "speech recognition unavailable; run Aegis with .venv\\Scripts\\python.exe"
    try:
        names = sr.Microphone.list_microphone_names()
        if not names:
            return "no microphones detected"
        return f"microphone ready: {len(names)} device(s); default input will be used"
    except OSError as exc:
        return f"microphone check failed: {exc}"


def listen(status_callback=None) -> str | None:

    if sr is None or _recognizer is None:
        print(microphone_status() + "; type a message instead.")
        return None

    try:
        with sr.Microphone() as source:
            _recognizer.adjust_for_ambient_noise(source, duration=0.5)
            print("listening... (speak now)")
            if status_callback:
                status_callback("listening")
            try:
                audio = _recognizer.listen(source, timeout=6, phrase_time_limit=15)
            except sr.WaitTimeoutError:
                print("didn't hear anything.")
                if status_callback:
                    status_callback("timeout")
                return None
    except (AttributeError, OSError) as exc:
        print(f"microphone unavailable: {exc}")
        if status_callback:
            status_callback("error")
        return None

    if status_callback:
        status_callback("transcribing")

    # SpeechRecognition does not expose a request timeout for recognize_google.
    # Keep a stalled network request from leaving the UI in transcribing state.
    result_queue = Queue(maxsize=1)
    recognizer = _recognizer
    recognize_google = getattr(recognizer, "recognize_google")

    def transcribe() -> None:
        try:
            result_queue.put(("text", recognize_google(audio)))
        except Exception as exc:
            if type(exc).__name__ == "UnknownValueError":
                result_queue.put(("empty", None))
            elif type(exc).__name__ == "RequestError":
                result_queue.put(("error", f"speech recognition service error: {exc}"))
            else:
                result_queue.put(("error", f"speech recognition failed: {exc}"))

    threading.Thread(target=transcribe, daemon=True).start()
    try:
        result_type, result = result_queue.get(timeout=_TRANSCRIPTION_TIMEOUT_SECONDS)
    except Empty:
        print("speech recognition timed out; check your internet connection and try again.")
        return None

    if result_type == "empty":
        print("couldn't understand that — try again.")
        return None
    if result_type == "error":
        print(result)
        return None
    return result

