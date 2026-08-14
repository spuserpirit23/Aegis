import os
import json
import time
from pathlib import Path
from google import genai

from skills.weather import get_weather, TOOL_SCHEMA as WEATHER_SCHEMA
from brain.mind import Mind
from brain.trust import trust_level
from brain.emotion import compute_emotion

MODEL = "gemini-3.6-flash"


_TOOL_USE_INSTRUCTION = (
    "Use the weather tool when asked about weather. When the user "
    "shares something worth remembering long-term — their name or "
    "nickname, a stated preference, an ongoing project — call "
    "remember_fact to save it. Don't call it for one-off "
    "conversational details that don't need to persist."
)

mind = Mind()
mind.load()

# NOTE: SYSTEM_INSTRUCTION is no longer built once at import time.
# Trust/emotion change per-turn based on memory state, so the system
# prompt now has to be assembled per-turn too (see Planner.respond).
# Static identity/personality/values/communication YAML is still only
# parsed once (Mind.load() above) — only the [CURRENT STATE] section
# and the by_trust tone line change turn to turn.

REMEMBER_SCHEMA = {
    "type": "function",
    "name": "remember_fact",
    "description": (
        "Save a durable fact about the user for future conversations "
        "— their name/nickname, preferences, ongoing projects, etc. "
        "Not for one-off details that don't need to persist."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Short label, e.g. 'nickname', 'name', 'project'",
            },
            "value": {
                "type": "string",
                "description": "The fact to remember",
            },
        },
        "required": ["key", "value"],
    },
}

TOOLS = [WEATHER_SCHEMA, REMEMBER_SCHEMA]

_INTERACTION_KEY = "_last_interaction_id"


def load_env_file(path: str | None = None) -> dict[str, str]:
    """Loads KEY=VALUE pairs from a .env file into os.environ, without
    overwriting variables already set in the real environment."""
    env_path = Path(path or os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"\'')
        if key and value:
            os.environ.setdefault(key, value)
            values[key] = value
    return values


class PlannerFallbackError(RuntimeError):
    """Raised when the planner is asked to use Gemini without a configured key."""


class Planner:
    def __init__(self, memory):
        load_env_file()
        api_key = os.environ.get("GEMINI_API_KEY")
        self.memory = memory
        self.uses_fallback = not api_key
        self.client = None
        self.skills = {}
        # Set each turn in respond(); exposed so a UI/demo script can
        # read planner.last_trust / planner.last_emotion after a turn
        # to show "here's what the mind knows" on screen.
        self.last_trust = "medium"
        self.last_emotion = "neutral"

        if not api_key:
            return

        self.client = genai.Client(api_key=api_key)
        self.skills = {
            "get_weather": get_weather,
            "remember_fact": self._remember_fact,
        }

    def _remember_fact(self, key: str, value: str) -> dict:
        self.memory.remember_fact(key, value)
        return {"ok": True, "saved": {key: value}}

    def _build_system_instruction(self) -> str:
        trust = trust_level(self.memory)
        emotion = compute_emotion(self.memory, trust)
        self.last_trust = trust
        self.last_emotion = emotion

        system = mind.as_prompt(trust=trust, emotion=emotion)
        system += "\n\n[TOOL USE]\n" + _TOOL_USE_INSTRUCTION
        if self.memory.facts_as_text():
            system += "\n\n" + self.memory.facts_as_text()
        return system

    def respond(self, user_text: str) -> str:
        self.memory.add_turn("user", user_text)

        if self.uses_fallback:
            fallback = (
                "Gemini is not configured right now, so I can only provide a "
                "basic fallback response. Set GEMINI_API_KEY to enable full AI replies."
            )
            self.memory.add_turn("assistant", fallback)
            return fallback

        # Trust/emotion computed AFTER add_turn so this turn's message
        # is part of what emotion.py reads, but the score/state used
        # for tone reflects the user's state walking into this reply.
        system = self._build_system_instruction()

        prev_id = self.memory.facts.get(_INTERACTION_KEY)
        kwargs = dict(
            model=MODEL,
            input=user_text,
            tools=TOOLS,
            system_instruction=system,
        )
        if prev_id:
            kwargs["previous_interaction_id"] = prev_id

        t0 = time.monotonic()
        interaction = self.client.interactions.create(**kwargs)
        t1 = time.monotonic()
        print(f"[timing] first call: {t1 - t0:.2f}s, stop reason: "
              f"{'tool_use' if any(s.type == 'function_call' for s in interaction.steps) else 'text'}")
        print(f"[state] trust={self.last_trust} emotion={self.last_emotion}")
        call_count = 1

        while True:
            fc_steps = [s for s in interaction.steps if s.type == "function_call"]
            if not fc_steps:
                break

            results_input = []
            for step in fc_steps:
                skill_fn = self.skills.get(step.name)
                result = (
                    skill_fn(**step.arguments)
                    if skill_fn
                    else {"ok": False, "error": f"Unknown skill '{step.name}'"}
                )
                results_input.append({
                    "type": "function_result",
                    "name": step.name,
                    "call_id": step.id,
                    "result": [{"type": "text", "text": json.dumps(result)}],
                })

            tN = time.monotonic()
            interaction = self.client.interactions.create(
                model=MODEL,
                previous_interaction_id=interaction.id,
                tools=TOOLS,
                system_instruction=system,
                input=results_input,
            )
            tN1 = time.monotonic()
            call_count += 1
            print(f"[timing] tool-result call #{call_count}: {tN1 - tN:.2f}s")

        print(f"[timing] total: {time.monotonic() - t0:.2f}s across {call_count} API call(s)")

        final_text = interaction.output_text
        self.memory.remember_fact(_INTERACTION_KEY, interaction.id)
        self.memory.add_turn("assistant", final_text)
        return final_text
