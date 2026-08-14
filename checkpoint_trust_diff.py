"""
Integration checkpoint — this is the actual demo, not just a test.

Runs the SAME user message through the mind with two memory states:
  1. fresh (empty memory.json)          -> should score low trust
  2. your real memory.json              -> should score high trust

Prints trust/emotion + the resulting [CURRENT STATE] and [COMMUNICATION]
prompt sections side by side. Doesn't call Gemini (no API cost, no
network needed) — it verifies the INPUT the model receives actually
differs, which is what you're claiming when you say "trust changes
behavior." If this diff looks the same for low vs high, the demo
will silently fail even if Gemini's output happens to sound fine.

Run: python3 checkpoint_trust_diff.py /path/to/your/memory.json
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from brain.mind import Mind
from brain.trust import trust_level, compute_trust_score
from brain.emotion import compute_emotion


class FakeMemory:
    def __init__(self, data):
        self.turns = data.get("turns", [])
        self.facts = data.get("facts", {})


def extract_section(prompt: str, tag: str) -> str:
    marker = f"[{tag}]"
    if marker not in prompt:
        return "(missing)"
    start = prompt.index(marker)
    rest = prompt[start:]
    next_bracket = rest.find("\n[", 1)
    return rest if next_bracket == -1 else rest[:next_bracket]


def run(memory_path: str | None):
    mind = Mind()
    mind.load()

    fresh = FakeMemory({"turns": [], "facts": {}})

    if memory_path:
        real_data = json.loads(Path(memory_path).read_text())
    else:
        print("No memory.json path given — using a synthetic 'high trust' "
              "sample instead of your real file.\n")
        real_data = {
            "turns": [{"role": "user", "content": "hi"}] * 18,
            "facts": {"nickname": "Spirit", "name": "Arpan"},
        }
    real = FakeMemory(real_data)

    for label, mem in [("FRESH (should be low)", fresh), ("REAL (should be high)", real)]:
        score = compute_trust_score(mem)
        trust = trust_level(mem)
        emotion = compute_emotion(mem, trust)
        prompt = mind.as_prompt(trust=trust, emotion=emotion)

        print(f"=== {label} ===")
        print(f"score={score} trust={trust} emotion={emotion}")
        print(extract_section(prompt, "CURRENT STATE"))
        print(extract_section(prompt, "COMMUNICATION"))
        print()

    fresh_trust = trust_level(fresh)
    real_trust = trust_level(real)
    if fresh_trust == real_trust:
        print(f"⚠ WARNING: both scored '{fresh_trust}' — no visible "
              f"difference. This is what would fail live. Check that "
              f"the real memory.json actually has facts/turns, or that "
              f"trust.py thresholds match your data's scale.")
        sys.exit(1)
    else:
        print(f"✓ Trust differs: fresh={fresh_trust} vs real={real_trust}. "
              f"Prompt sections above should read differently — that's "
              f"the actual demo checkpoint. Read them, don't just trust "
              f"the labels.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    run(path)
