from brain.memory import Memory
from brain.planner import Planner
from voice.stt import listen
from voice.tts import speak


def main():
    memory = Memory(path="memory.json")
    planner = Planner(memory)

    print("Brain online (voice mode).")
    print("Press Enter to talk, or type a message instead. Type 'exit' to quit.\n")

    while True:
        try:
            typed = input("[Enter]=talk> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nshutting down.")
            break

        if typed.lower() in ("exit", "quit"):
            print("shutting down.")
            break

        if typed:
            user_text = typed
        else:
            user_text = listen()
            if not user_text:
                continue
            print(f"you said: {user_text}")

        reply = planner.respond(user_text)
        print(f"brain> {reply}\n")
        speak(reply)


if __name__ == "__main__":
    main()
