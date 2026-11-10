import argparse
import json
import re
import shutil
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_LEGACY_BUCKET = "_legacy"

# Shared bucket for anyone who hasn't confirmed a name. Never trusted (see
# trust.py) and never written to disk: several different people end up here,
# so nothing said by a guest may become a durable fact about "the user".
GUEST_KEY = "guest"

# Internal bookkeeping that used to be stored inside ``facts``. Facts are what
# Aegis *knows about a person* and are shown to the model and the UI; these
# keys are runtime plumbing and live in the bucket's ``state`` dict instead.
INTERNAL_STATE_KEYS = ("_last_interaction_id", "_correction_count")

_NAME_TOKEN = r"([A-Za-z][A-Za-z\-']{1,30})"

# Explicit self-identification only. A bare "I'm X" / "I am X" inside a
# sentence is NOT treated as a name: "I am very happy today" produced a user
# called "very". The I'm/I am forms are accepted only when the whole
# utterance is the introduction ("hey, I'm Spirit"), and even then the result
# is only a *candidate* that the planner must confirm with the speaker.
_EXPLICIT_NAME_PATTERNS = [
    re.compile(rf"\bmy name is\s+{_NAME_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\bmy name's\s+{_NAME_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\bcall me\s+{_NAME_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\bi go by\s+{_NAME_TOKEN}\b", re.IGNORECASE),
]
_GREETING = r"(?:(?:hi|hey|hello|hii|hye|yo|ok|okay|so)[\s,!.]+)*"
_INTRODUCTION_PATTERNS = [
    re.compile(rf"^\s*{_GREETING}(?:i'?m|i am|this is|it'?s)\s+{_NAME_TOKEN}"
               rf"(?:\s+(?:here|speaking|this side))?\s*[.!]*\s*$", re.IGNORECASE),
]

# Words that can follow an introduction phrase but are not names.
_NAME_STOPWORDS = {
    "not", "sure", "fine", "good", "okay", "ok", "done", "here", "back",
    "sorry", "confused", "trying", "working", "just", "still", "also",
    "very", "so", "really", "happy", "sad", "tired", "busy", "bored",
    "great", "well", "alright", "going", "testing", "home", "ready",
    "glad", "excited", "angry", "upset", "the", "a", "an", "your", "you",
    "me", "it", "that", "this", "what", "who", "aegis",
}

_YES = re.compile(r"^\s*(yes|yeah|yep|yup|ya|haan|han|correct|right|exactly|"
                  r"that'?s (?:me|right|correct)|sure|confirmed?|affirmative)\b", re.IGNORECASE)
_NO = re.compile(r"^\s*(no|nope|nah|nahi|wrong|incorrect|not really|that'?s not)\b", re.IGNORECASE)


def _clean_candidate(raw: str) -> str | None:
    candidate = raw.strip(" '-")
    if len(candidate) < 2 or candidate.lower() in _NAME_STOPWORDS:
        return None
    return candidate


def extract_name(text: str) -> str | None:
    """Return a *candidate* name from an explicit self-introduction, or None.

    This is not identity verification and not even a commitment: the planner
    asks the speaker to confirm before any user bucket is created or switched.
    """
    for pattern in _EXPLICIT_NAME_PATTERNS + _INTRODUCTION_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = _clean_candidate(match.group(1))
            if candidate:
                return candidate
    return None


def parse_confirmation(text: str) -> bool | None:
    """True for an affirmative reply, False for a negative one, else None."""
    if _NO.search(text):
        return False
    if _YES.search(text):
        return True
    return None


def _user_key(name: str) -> str:
    """Normalize a name into a stable lookup key: 'Spirit' and
    'spirit' and ' Spirit ' should all hit the same user bucket."""
    return name.strip().lower()


def _new_bucket(display_name: str | None) -> dict:
    facts = {"name": display_name} if display_name else {}
    return {"turns": [], "facts": facts, "state": {}}


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
    def is_guest(self) -> bool:
        return self.key == GUEST_KEY

    @property
    def _bucket(self) -> dict:
        return self.store.data["users"][self.key]

    @property
    def turns(self) -> list:
        return self._bucket["turns"]

    @property
    def facts(self) -> dict:
        return self._bucket["facts"]

    @property
    def state(self) -> dict:
        return self._bucket.setdefault("state", {})

    def add_turn(self, role: str, content: str):
        turns = self._bucket["turns"]
        turns.append({"role": role, "content": content})
        self._bucket["turns"] = turns[-self.store.max_turns:]
        self.store.save()

    def recent(self):
        return [{"role": t["role"], "content": t["content"]} for t in self.turns]

    def remember_fact(self, key: str, value: str) -> bool:
        """Store a durable fact. Refused for the shared guest bucket and for
        internal bookkeeping keys (use set_state for those)."""
        if self.is_guest or key.startswith("_"):
            return False
        self._bucket["facts"][key] = value
        self.store.save()
        return True

    def get_state(self, key: str, default=None):
        return self.state.get(key, default)

    def set_state(self, key: str, value) -> None:
        if value is None:
            self.state.pop(key, None)
        else:
            self.state[key] = value
        self.store.save()

    def clear_conversation(self) -> None:
        """Forget turns and runtime state; keep durable facts."""
        self._bucket["turns"] = []
        self._bucket["state"] = {}
        self.store.save()

    def facts_as_text(self) -> str:
        facts = {k: v for k, v in self.facts.items() if not k.startswith("_")}
        if not facts:
            return ""
        lines = [f"- {k}: {v}" for k, v in facts.items()]
        return "Known facts about the user:\n" + "\n".join(lines)


class Memory:
    """Multi-user store. Holds all users' data; hand out a UserMemory
    view via get_user(name) for whoever's currently talking."""

    def __init__(self, path: str = "memory.json", max_turns: int = 20):
        requested_path = Path(path)
        self.path = (
            requested_path
            if requested_path.is_absolute()
            else _PROJECT_ROOT / requested_path
        )
        self.max_turns = max_turns
        self.data = {"users": {}, "last_user": None}
        self._load()

    def _load(self):
        if not self.path.exists():
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return
        if not isinstance(raw, dict):
            return

        if "users" in raw:
            self.data = raw
            self.data.setdefault("last_user", None)
        else:
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
        if self._normalize():
            self.save()

    def _normalize(self) -> bool:
        """Bring loaded data up to the current schema. Returns True if
        anything changed. Idempotent."""
        changed = False
        users = self.data["users"]
        if GUEST_KEY in users:
            # Older versions persisted the shared guest bucket, which mixed
            # several speakers' turns and "facts". Never reload it.
            del users[GUEST_KEY]
            changed = True
        for bucket in users.values():
            bucket.setdefault("turns", [])
            facts = bucket.setdefault("facts", {})
            state = bucket.setdefault("state", {})
            for key in [k for k in facts if k.startswith("_")]:
                value = facts.pop(key)
                if key in INTERNAL_STATE_KEYS:
                    state.setdefault(key, value)
                changed = True
        if self.data.get("last_user") not in users:
            changed = changed or self.data.get("last_user") is not None
            self.data["last_user"] = None
        return changed

    def save(self):
        last_user = self.data.get("last_user")
        persisted = {
            "users": {k: v for k, v in self.data["users"].items() if k != GUEST_KEY},
            "last_user": None if last_user == GUEST_KEY else last_user,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(persisted, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _view(self, key: str, fallback_name: str) -> UserMemory:
        if key == GUEST_KEY:
            return UserMemory(self, key, "Guest")
        name = self.data["users"][key]["facts"].get("name", fallback_name)
        return UserMemory(self, key, name)

    def get_user(self, display_name: str) -> UserMemory:
        """Fetch (or create) the bucket for a given confirmed name."""
        key = _user_key(display_name)
        if key not in self.data["users"]:
            is_guest = key == GUEST_KEY
            self.data["users"][key] = _new_bucket(None if is_guest else display_name.strip())
            if not is_guest:
                self.save()
        self.data["last_user"] = key
        return self._view(key, display_name.strip())

    def peek_user(self, display_name: str) -> UserMemory | None:
        """Return an existing user's view without creating or selecting it."""
        key = _user_key(display_name)
        if key not in self.data["users"]:
            return None
        return self._view(key, display_name.strip())

    def known_users(self) -> list[str]:
        """Display names of everyone Aegis has a bucket for (excluding guest)."""
        return [
            bucket.get("facts", {}).get("name", key)
            for key, bucket in self.data["users"].items()
            if key != GUEST_KEY
        ]

    def has_user(self, display_name: str) -> bool:
        return _user_key(display_name) in self.data["users"]


def repair(path: str, drop: list[str], dry_run: bool = False) -> dict:
    """Normalize a memory file (strip internal keys from facts, drop the
    persisted guest bucket) and remove the named user buckets. A timestamped
    backup is written next to the file before anything is changed."""
    target = Path(path)
    if not target.is_absolute():
        target = _PROJECT_ROOT / target
    before = json.loads(target.read_text(encoding="utf-8"))
    if "users" not in before:
        raise SystemExit("refusing to repair: file uses the pre-multi-user schema")

    mem = Memory.__new__(Memory)
    mem.path, mem.max_turns = target, 20
    mem.data = json.loads(json.dumps(before))
    mem.data.setdefault("last_user", None)
    report = {"file": str(target), "dropped": [], "missing": [], "backup": None,
              "guest_removed": GUEST_KEY in mem.data["users"]}
    for name in drop:
        key = _user_key(name)
        if key in mem.data["users"] and key != GUEST_KEY:
            del mem.data["users"][key]
            report["dropped"].append(key)
        elif key != GUEST_KEY:
            report["missing"].append(key)
    mem._normalize()

    if dry_run:
        return report
    backup = target.with_name(f"{target.stem}.backup-{time.strftime('%Y%m%d-%H%M%S')}{target.suffix}")
    shutil.copy2(target, backup)
    report["backup"] = str(backup)
    mem.save()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repair an Aegis memory file.")
    parser.add_argument("--path", default="memory.json")
    parser.add_argument("--drop", nargs="*", default=[],
                        help="user keys to delete (e.g. mis-extracted names)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(args.path, args.drop, args.dry_run), indent=2))
