import json
from pathlib import Path


class Memory:
    def __init__(self, path: str = "memory.json", max_turns: int = 20):
        self.path = Path(path)
        self.max_turns = max_turns
        self.turns = []
        self.facts = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return

        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            self.turns = []
            self.facts = {}
            return

        self.turns = data.get("turns", [])
        self.facts = data.get("facts", {})

    def save(self):
        self.path.write_text(
            json.dumps({"turns": self.turns, "facts": self.facts}, indent=2)
        )

    def add_turn(self, role: str, content: str):
        self.turns.append({"role": role, "content": content})
        # keep only the last N turns so the context sent to the model
        # doesn't grow without bound
        self.turns = self.turns[-self.max_turns:]
        self.save()

    def recent(self):
        """Return turns formatted for the Anthropic messages API."""
        return [{"role": t["role"], "content": t["content"]} for t in self.turns]

    def remember_fact(self, key: str, value: str):
        self.facts[key] = value
        self.save()

    def facts_as_text(self) -> str:
        if not self.facts:
            return ""
        lines = [f"- {k}: {v}" for k, v in self.facts.items()]
        return "Known facts about the user:\n" + "\n".join(lines)
