import json
import re
from pathlib import Path

_LEGACY_BUCKET = "_legacy"

_NAME_PATTERNS = [
    re.compile(r"\bi'?m\s+([a-zA-Z][a-zA-Z\-']{0,30})\b", re.IGNORECASE),
    re.compile(r"\bi am\s+([a-zA-Z][a-zA-Z\-']{0,30})\b", re.IGNORECASE),
    re.compile(r"\bmy name is\s+([a-zA-Z][a-zA-Z\-']{0,30})\b", re.IGNORECASE),
    re.compile(r"\bcall me\s+([a-zA-Z][a-zA-Z\-']{0,30})\b", re.IGNORECASE),
]

# Words that would false-positive on "I'm ___" patterns (states, not names).
_NAME_STOPWORDS = {
    "not", "sure", "fine", "good", "okay", "ok", "done", "here", "back",
    "sorry", "confused", "trying", "working", "just", "still", "also",
}


def extract_name(text: str) -> str | None:
    """Best-effort, self-declared name extraction. Not verification —
    just a heuristic to avoid asking 'who are you' every single turn
    once someone has already said their name naturally."""
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = match.group(1).strip()
            if candidate.lower() in _NAME_STOPWORDS:
                continue
            return candidate
    return None


def _user_key(name: str) -> str:
    """Normalize a name into a stable lookup key: 'Spirit' and
    'spirit' and ' Spirit ' should all hit the same user bucket."""
    return name.strip().lower()


class UserMemory:
    """Same interface as the old single-user Memory (turns, facts,
    add_turn, remember_fact, facts_as_text) so trust.py, emotion.py,
    and planner.py don't need to know multi-user storage exists
    underneath — they just get handed the current user's view."""

    def __init__(self, store: "Memory", key: str, display_name: str):
        self.store = store
        self.key = key
        self.display_name = display_name

    @property
    def _bucket(self) -> dict:
        return self.store.data["users"][self.key]

    @property
    def turns(self) -> list:
        return self._bucket["turns"]

    @property
    def facts(self) -> dict:
        return self._bucket["facts"]

    def add_turn(self, role: str, content: str):
        turns = self._bucket["turns"]
        turns.append({"role": role, "content": content})
        self._bucket["turns"] = turns[-self.store.max_turns:]
        self.store.save()

    def recent(self):
        return [{"role": t["role"], "content": t["content"]} for t in self.turns]

    def remember_fact(self, key: str, value: str):
        self._bucket["facts"][key] = value
        self.store.save()

    def facts_as_text(self) -> str:
        facts = self.facts
        if not facts:
            return ""
        lines = [f"- {k}: {v}" for k, v in facts.items()]
        return "Known facts about the user:\n" + "\n".join(lines)


class Memory:
    """Multi-user store. Holds all users' data; hand out a UserMemory
    view via get_user(name) for whoever's currently talking."""

    def __init__(self, path: str = "memory.json", max_turns: int = 20):
        self.path = Path(path)
        self.max_turns = max_turns
        self.data = {"users": {}, "last_user": None}
        self._load()

    def _load(self):
        if not self.path.exists():
            return

        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return

        if "users" in raw:
            self.data = raw
            self.data.setdefault("last_user", None)
            return

        # Old flat schema — migrate into a single "_legacy" user bucket
        # rather than discarding existing history.
        self.data = {
            "users": {
                _LEGACY_BUCKET: {
                    "turns": raw.get("turns", []),
                    "facts": raw.get("facts", {}),
                }
            },
            "last_user": _LEGACY_BUCKET,
        }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2))

    def get_user(self, display_name: str) -> UserMemory:
        """Fetch (or create) the bucket for a given self-declared name."""
        key = _user_key(display_name)
        if key not in self.data["users"]:
            self.data["users"][key] = {"turns": [], "facts": {"name": display_name}}
            self.save()
        self.data["last_user"] = key
        return UserMemory(self, key, display_name)

    def known_users(self) -> list[str]:
        """Display names of everyone Aegis has a bucket for."""
        names = []
        for key, bucket in self.data["users"].items():
            names.append(bucket.get("facts", {}).get("name", key))
        return names

    def has_user(self, display_name: str) -> bool:
        return _user_key(display_name) in self.data["users"]
