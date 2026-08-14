from pathlib import Path

import yaml

_LOAD_ORDER = ["identity.yaml", "personality.yaml", "communication.yaml", "values.yaml"]
_MIND_DIR = Path(__file__).parent.parent / "mind"

_VALID_TRUST_LEVELS = {"low", "medium", "high"}
_VALID_EMOTIONS = {"happy", "thinking", "concerned", "neutral"}


class Mind:
    def __init__(self):
        self.sections: dict[str, dict] = {}
        self.loaded = False

    def load(self) -> "Mind":
        self.sections = {}
        for filename in _LOAD_ORDER:
            path = _MIND_DIR / filename
            if not path.exists():
                continue
            key = filename.removesuffix(".yaml")
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                raise ValueError(f"Malformed YAML in {path}: {e}") from e
            self.sections[key] = data or {}
        self.loaded = True
        return self

    def as_prompt(self, trust: str = "medium", emotion: str = "neutral") -> str:
        if not self.loaded:
            self.load()

        if trust not in _VALID_TRUST_LEVELS:
            trust = "medium"
        if emotion not in _VALID_EMOTIONS:
            emotion = "neutral"

        parts = [
            self._render_identity(),
            self._render_personality(),
            self._render_communication(trust),
            self._render_values(),
            self._render_state(trust, emotion),
        ]
        return "\n\n".join(p for p in parts if p)

    # -- section renderers -------------------------------------------
    # Each turns structured YAML into readable prompt text. Deliberately
    # simple string formatting, not a templating engine — easy to read,
    # easy to diff when someone edits a YAML file.

    def _render_identity(self) -> str:
        d = self.sections.get("identity")
        if not d:
            return ""
        builder = d.get("builder", {})
        status = d.get("status", {})
        traits = d.get("traits", [])
        lines = [
            f"You are {d.get('name', 'Aegis')}, {d.get('role', '')}.",
            f"Your builder is {builder.get('name', '')}, who goes by {builder.get('nickname', '')}.",
            f"Current embodiment: {status.get('embodiment', 'unknown')}.",
        ]
        if status.get("note"):
            lines.append(status["note"].strip())
        if traits:
            lines.append("Traits: " + ", ".join(traits) + ".")
        return "[IDENTITY]\n" + "\n".join(lines)

    def _render_personality(self) -> str:
        d = self.sections.get("personality")
        if not d:
            return ""
        traits = d.get("traits", {})
        lines = []
        for name, spec in traits.items():
            if not isinstance(spec, dict):
                continue
            lines.append(f"- {name} ({spec.get('level', '')}): {spec.get('behavior', '')}")
        return "[PERSONALITY]\n" + "\n".join(lines)

    def _render_communication(self, trust: str) -> str:
        d = self.sections.get("communication")
        if not d:
            return ""
        lines = [f"Tone: {d.get('tone', '')}"]
        for item in d.get("style", []):
            lines.append(f"- {item}")

        by_trust = d.get("by_trust", {})
        adjustment = by_trust.get(trust, {})
        if adjustment.get("tone"):
            lines.append(f"Given current trust level ({trust}): {adjustment['tone']}")

        return "[COMMUNICATION]\n" + "\n".join(lines)

    def _render_values(self) -> str:
        d = self.sections.get("values")
        if not d:
            return ""
        lines = [f"- {p}" for p in d.get("principles", [])]
        return "[VALUES]\n" + "\n".join(lines)

    def _render_state(self, trust: str, emotion: str) -> str:
        # This is the per-turn seam: trust/emotion computed in
        # brain/trust.py and brain/emotion.py, shown here as plain
        # context, not as separate reasoning the model has to perform.
        return (
            "[CURRENT STATE]\n"
            f"trust_level: {trust}\n"
            f"emotion: {emotion}\n"
            "Let trust_level and emotion inform tone per the "
            "COMMUNICATION section above. Don't mention these labels "
            "explicitly to the user — express them, don't narrate them."
        )
