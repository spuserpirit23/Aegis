import sys
import shutil
import subprocess
import threading
from pathlib import Path

from brain.memory import Memory
from brain.planner import Planner
from voice.stt import listen
from voice.tts import speak


def build_planner() -> Planner:
    memory = Memory(path="memory.json")
    return Planner(memory)


def main():
    planner = build_planner()

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


def ui_main():
    from aegis_bridge import create_server

    project_dir = Path(__file__).resolve().parent
    server = create_server(build_planner())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Aegis bridge online at http://127.0.0.1:8765")

    godot_exe = _find_godot()
    if godot_exe is None:
        print("Godot was not found. Open godot_ui/project.godot manually.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return

    try:
        subprocess.run([godot_exe, "--path", str(project_dir / "godot_ui")], check=False)
    finally:
        server.shutdown()


def _find_godot() -> str | None:
    found = shutil.which("godot") or shutil.which("godot4")
    if found:
        return found

    candidates = (
        Path.home() / "AppData/Local/Godot/godot.exe",
        Path.home() / "AppData/Local/Programs/Godot/godot.exe",
        Path("C:/Program Files/Godot/godot.exe"),
        Path("C:/Program Files (x86)/Godot/godot.exe"),
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    downloads = Path.home() / "Downloads"
    # Check directly in Downloads or nested folder builds
    patterns = [
        "Godot_v4.7*.exe/Godot_v4.7*_win64.exe",
        "Godot_v4*.exe/Godot_v4*_win64.exe",
        "Godot_v4*.exe/Godot_v4*.exe",
        "Godot_v4*.exe",
        "Godot*.exe",
    ]
    for pattern in patterns:
        matches = sorted(downloads.glob(pattern), reverse=True)
        for match in matches:
            if match.is_file():
                return str(match)
    return None


if __name__ == "__main__":
    if "--cli" in sys.argv:
        main()
    else:
        ui_main()
