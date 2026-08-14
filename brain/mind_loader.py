
from pathlib import Path

_MIND_DIR = Path(__file__).parent.parent / "mind"
_LOAD_ORDER = ["identity.md", "personality.md", "communication.md"]


def load_mind() -> str:
    sections = []
    for filename in _LOAD_ORDER:
        path = _MIND_DIR / filename
        if not path.exists():
            continue
        title = filename.removesuffix(".md").upper()
        sections.append(f"[{title}]\n{path.read_text().strip()}")

    if not sections:
        return ""

    return "\n\n".join(sections)
