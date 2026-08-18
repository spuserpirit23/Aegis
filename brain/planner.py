import os
import json
import time
from pathlib import Path
from google import genai

from skills.weather import get_weather, TOOL_SCHEMA as WEATHER_SCHEMA
from brain.mind import Mind
from brain.trust import trust_level
from brain.emotion import compute_emotion
from brain.memory import extract_name
from brain.permission import check_permission

MODEL = "gemini-3.6-flash"

_GUEST_KEY = "guest"

_TOOL_USE_INSTRUCTION = (
    "Use the weather tool when asked about weather. When the user "
    "shares something worth remembering long-term — their name or "
    "nickname, a stated preference, an ongoing project — call "
    "remember_fact to save it. Don't call it for one-off "
    "conversational details that don't need to persist."
)

_ASK_NAME_INSTRUCTION = (
    "You don't know who you're talking to yet. Naturally ask their "
    "name early in this reply — don't make it the whole reply, just "
    "work it in. Once they answer, you'll recognize them in future "
    "conversations."
)

mind = Mind()
mind.load()

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
            "key": {"type": "string", "description": "Short label, e.g. 'nickname', 'name', 'project'"},
            "value": {"type": "string", "description": "The fact to remember"},
        },
        "required": ["key", "value"],
    },
}

TOOLS = [WEATHER_SCHEMA, REMEMBER_SCHEMA]

_INTERACTION_KEY = "_last_interaction_id"


def load_env_file(path: str | None = None) -> dict[str, str]:
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
    pass


class Planner:
    """
    NOTE on identity: self.memory is now the multi-user Memory store
    (memory.py), not a single user's data. self.current_user is a
    UserMemory view for whoever we currently believe we're talking
    to — resolved by self-declared name only (extract_name() heuristic,
    or the model's own remember_fact call). This is NOT verified
    identity — no voice or face recognition. Anyone can claim to be
    anyone. That's a known, deliberate limitation for this phase, not
    an oversight — voice/face verification is roadmap, post-hardware.
    """

    def __init__(self, memory):
        load_env_file()
        api_key = os.environ.get("GEMINI_API_KEY")
        self.memory = memory  # multi-user Memory
        self.current_user = None  # UserMemory, set once identified
        self._asked_name_this_session = False
        self.uses_fallback = not api_key
        self.client = None
        self.skills = {}
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
        if key.lower() in ("name", "nickname") and self.current_user is None:
            self._set_user(value)
        if self.current_user is not None:
            self.current_user.remember_fact(key, value)
            return {"ok": True, "saved": {key: value}}
        return {"ok": False, "error": "no current user resolved yet"}

    def _set_user(self, display_name: str):
        self.current_user = self.memory.get_user(display_name)
        self._asked_name_this_session = False

    def _resolve_speaker(self, user_text: str):
        """Self-declared identity only — see class docstring. Falls
        back to a shared 'guest' bucket (always low trust, by design)
        until a name is captured."""
        if self.current_user is not None:
            return

        name = extract_name(user_text)
        if name:
            self._set_user(name)
        else:
            self.current_user = self.memory.get_user(_GUEST_KEY)

    def _build_system_instruction(self) -> str:
        trust = trust_level(self.current_user)
        emotion = compute_emotion(self.current_user, trust)
        self.last_trust = trust
        self.last_emotion = emotion

        system = mind.as_prompt(trust=trust, emotion=emotion)
        system += "\n\n[TOOL USE]\n" + _TOOL_USE_INSTRUCTION

        is_unidentified = self.current_user.key == _GUEST_KEY
        if is_unidentified and not self._asked_name_this_session:
            system += "\n\n[IDENTITY]\n" + _ASK_NAME_INSTRUCTION
            self._asked_name_this_session = True

        if self.current_user.facts_as_text():
            system += "\n\n" + self.current_user.facts_as_text()
        return system

    def respond(self, user_text: str) -> str:
        self._resolve_speaker(user_text)
        self.current_user.add_turn("user", user_text)

        if self.uses_fallback:
            fallback = (
                "Gemini is not configured right now, so I can only provide a "
                "basic fallback response. Set GEMINI_API_KEY to enable full AI replies."
            )
            self.current_user.add_turn("assistant", fallback)
            return fallback

        system = self._build_system_instruction()

        prev_id = self.current_user.facts.get(_INTERACTION_KEY)
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
        print(f"[state] user={self.current_user.key} trust={self.last_trust} emotion={self.last_emotion}")
        call_count = 1

        while True:
            fc_steps = [s for s in interaction.steps if s.type == "function_call"]
            if not fc_steps:
                break

            results_input = []
            for step in fc_steps:
                # Permission check happens here, at the point of
                # execution, independent of trust_level computed
                # above. This is the actual enforcement of "trust !=
                # permission" — see brain/permission.py docstring.
                # No confirmed=True path wired yet: any skill in the
                # DEVICE_CONTROL/SENSITIVE_ACTION/COMMAND_ACTION tiers
                # will always be denied for now, since there's no UX
                # for the model to collect and replay a confirmation
                # across turns. That's a real gap, not silently
                # papered over — see note below.
                perm = check_permission(step.name)
                if not perm.allowed:
                    result = {
                        "ok": False,
                        "error": perm.reason,
                        "requires_confirmation": perm.requires_confirmation,
                    }
                else:
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
        self.current_user.remember_fact(_INTERACTION_KEY, interaction.id)
        self.current_user.add_turn("assistant", final_text)
        return final_text
