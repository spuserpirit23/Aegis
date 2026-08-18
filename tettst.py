import sys
sys.path.insert(0, "/home/claude")
from brain.memory import Memory, extract_name

# --- extract_name tests ---
cases = [
    ("hi I'm Spirit", "Spirit"),
    ("hey I am Arpan", "Arpan"),
    ("my name is Jordan", "Jordan"),
    ("call me Jay", "Jay"),
    ("I'm not sure", None),
    ("I'm just checking", None),
    ("what's the weather", None),
    ("I'm fine thanks", None),
]
for text, expected in cases:
    got = extract_name(text)
    status = "ok" if got == expected else "FAIL"
    print(f"{status}  {text!r} -> {got!r} (expected {expected!r})")

# --- Memory multi-user isolation ---
import tempfile, os, json
fd, path = tempfile.mkstemp(suffix=".json")
os.close(fd)

mem = Memory(path=path)
spirit = mem.get_user("Spirit")
spirit.add_turn("user", "hi I'm Spirit")
spirit.remember_fact("nickname", "Spirit")

jordan = mem.get_user("Jordan")
jordan.add_turn("user", "hi I'm Jordan")

print("\nSpirit facts:", spirit.facts)
print("Jordan facts:", jordan.facts)
print("Spirit turns count:", len(spirit.turns))
print("Jordan turns count:", len(jordan.turns))
assert "nickname" not in jordan.facts, "LEAK: Jordan sees Spirit's facts"
assert spirit.key != jordan.key
print("\nisolation ok: no cross-user leakage")

# reload from disk, confirm persistence + same-name-returns-same-bucket
mem2 = Memory(path=path)
spirit_again = mem2.get_user("spirit")  # different case
print("\nCase-insensitive lookup facts:", spirit_again.facts)
assert spirit_again.facts.get("nickname") == "Spirit", "case-insensitive lookup failed"
print("persistence + case-insensitive key ok")

os.remove(path)
