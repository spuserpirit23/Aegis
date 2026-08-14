"""
Live end-to-end trust test — talks to real Gemini, no permanent files.

Uses tempfile so both a "fresh" (low trust) and a "seeded" (high
trust) Memory exist as throwaway files that get deleted when the
script exits. Your real memory.json is never touched.

Needs GEMINI_API_KEY set (via .env or environment) — this makes real
API calls, so it costs a couple of requests each run. Don't run this
in a loop; run it once, read the two replies, decide if the tone
difference is convincing.

Run: python3 live_trust_test.py
"""

import sys
import json
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from brain.memory import Memory
from brain.planner import Planner

PROMPT = "hey, what do you think of the project so far"

# Enough turns + facts to push trust_level() to "high" per brain/trust.py
# thresholds — same shape as a real seasoned conversation, not your
# actual private history.
SEEDED_DATA = {
    "turns": [
        {"role": "user", "content": "hey I am spirit"},
        {"role": "assistant", "content": "Hey Spirit! Good to hear from you."},
    ] * 10,
    "facts": {
        "nickname": "Spirit",
        "name": "Arpan",
    },
}


def make_temp_memory(seed_data: dict | None) -> tuple[Memory, str]:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    if seed_data:
        Path(path).write_text(json.dumps(seed_data))
    else:
        Path(path).write_text(json.dumps({"turns": [], "facts": {}}))
    return Memory(path=path), path


def main():
    temp_paths = []

    try:
        fresh_mem, fresh_path = make_temp_memory(None)
        temp_paths.append(fresh_path)
        planner_fresh = Planner(fresh_mem)

        if planner_fresh.uses_fallback:
            print("GEMINI_API_KEY not set — this script needs a real key "
                  "to test actual model output. Set it in .env or the "
                  "environment and rerun.")
            return

        print(f"[fresh] trust will be computed on first respond() call")
        fresh_reply = planner_fresh.respond(PROMPT)
        print(f"\n=== FRESH (trust={planner_fresh.last_trust}, "
              f"emotion={planner_fresh.last_emotion}) ===")
        print(fresh_reply)

        seeded_mem, seeded_path = make_temp_memory(SEEDED_DATA)
        temp_paths.append(seeded_path)
        planner_seeded = Planner(seeded_mem)
        seeded_reply = planner_seeded.respond(PROMPT)
        print(f"\n=== SEEDED (trust={planner_seeded.last_trust}, "
              f"emotion={planner_seeded.last_emotion}) ===")
        print(seeded_reply)

        print("\n--- Read both replies above. Ask yourself: ---")
        print("1. Does SEEDED sound noticeably warmer/more casual than FRESH?")
        print("2. Does SEEDED reference shared history unprompted?")
        print("3. Would someone who didn't write the prompts notice the difference?")
        print("If any answer is 'not really', the fix is communication.yaml")
        print("wording, not trust.py/emotion.py — the input already differs")
        print("(checkpoint_trust_diff.py proved that); this is about whether")
        print("Gemini is weighting the instruction strongly enough.")

    finally:
        for p in temp_paths:
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    main()