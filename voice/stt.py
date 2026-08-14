try:
    import speech_recognition as sr
except ImportError:  # pragma: no cover - exercised when the dependency is absent
    sr = None

_recognizer = sr.Recognizer() if sr is not None else None


def listen() -> str | None:

    if sr is None or _recognizer is None:
        print("speech recognition unavailable; type a message instead.")
        return None

    try:
        with sr.Microphone() as source:
            _recognizer.adjust_for_ambient_noise(source, duration=0.5)
            print("listening... (speak now)")
            try:
                audio = _recognizer.listen(source, timeout=6, phrase_time_limit=15)
            except sr.WaitTimeoutError:
                print("didn't hear anything.")
                return None
    except (AttributeError, OSError) as exc:
        print(f"microphone unavailable: {exc}")
        return None

    try:
        return _recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        print("couldn't understand that — try again.")
        return None
    except sr.RequestError as e:
        print(f"speech recognition service error: {e}")
        return None
