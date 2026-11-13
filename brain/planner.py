import os
import json
import re
import threading
import time
from pathlib import Path
from typing import Optional
from google import genai

from skills.weather import get_weather, TOOL_SCHEMA as WEATHER_SCHEMA
from brain.mind import Mind
from brain.trust import trust_level
from brain.emotion import compute_emotion
from brain.emotion_engine import legacy_label
from brain.emotion_context import ResponseEmotionContext
from brain.emotion_providers import build_emotion_engine
from brain.event_bus import EventBus
from brain.memory import GUEST_KEY, extract_name, parse_confirmation
from brain.permission import can, check_permission
from brain.audit import actor_of, audit
from brain.identity import (AuthStatus, IdentityVerificationRequired, VerificationResult,
                            VerifierRegistry, default_registry)
from generation import GenerationRequest
from language.engine import LanguageEngine

MODEL = "gemini-3.6-flash"

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

# Stored in the user bucket's runtime ``state`` (never in ``facts``).
_INTERACTION_KEY = "_last_interaction_id"

# Facts that define who someone is go through the confirmation flow, never
# through free-text "my X is Y" capture or an unconfirmed tool call.
_IDENTITY_FACT_KEYS = ("name", "nickname")

_FALLBACK_REPLY = (
    "Gemini is not configured right now, so I can only provide a "
    "basic fallback response. Set GEMINI_API_KEY to enable full AI replies."
)

_EXPLICIT_FACT_PATTERNS = (
    re.compile(r"\bmy\s+([a-z][a-z0-9 _-]{1,30})\s+is\s+(.+)$", re.IGNORECASE),
    re.compile(r"\bremember\s+(?:that\s+)?my\s+([a-z][a-z0-9 _-]{1,30})\s+is\s+(.+)$", re.IGNORECASE),
)


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
    NOTE on identity: self.memory is the multi-user Memory store
    (memory.py). self.current_user is a UserMemory view for whoever we
    currently believe we're talking to. A name is only adopted after an
    explicit self-introduction ("my name is X", "call me X", or an
    utterance that is nothing but "I'm X") AND a "yes" when Aegis asks
    to confirm it. Until then the speaker is the shared, non-persistent
    guest. This is still NOT verified identity — no voice or face
    recognition; anyone can claim to be anyone. A confirmed claim gives
    auth_status "claimed", which trust caps at MEDIUM. A profile with an
    enrolled credential (brain/identity.py) cannot be entered by claiming
    its name at all: verify_identity() is the only way in, and it yields
    "verified", the only status that can reach HIGH trust.

    Concurrency: one turn at a time. respond(), reset_conversation() and
    set_user() hold self._lock, so overlapping bridge requests are
    serialized instead of interleaving user/emotion state.
    """

    def __init__(self, memory, verifiers: VerifierRegistry | None = None):
        load_env_file()
        api_key = os.environ.get("GEMINI_API_KEY")
        self.memory = memory  # multi-user Memory
        self._lock = threading.RLock()
        self.model = MODEL
        self.verifiers = verifiers or default_registry()
        # Always a valid view; starts as the shared, non-persistent guest.
        self.current_user = memory.get_user(GUEST_KEY)
        self._asked_name_this_session = False
        self.pending_name: str | None= None   # awaiting the speaker's "yes"
        self._identity_note: str | None = None  # per-turn [IDENTITY] guidance
        self.uses_fallback = not api_key
        self.client = None
        self.language_engine = None
        self.skills = {}
        self.last_trust = trust_level(self.current_user)
        self.last_emotion = "neutral"
        # Structured, entity-aware emotion state. Perception of the user stays
        # separate from AEGIS's own regulated state; the engine never replies.
        self.event_bus = EventBus()
        if api_key:
            self.client = genai.Client(api_key=api_key)
        # Provider comes from AEGIS_EMOTION_PROVIDER (default: rule-based).
        self.emotion_engine = build_emotion_engine(event_bus=self.event_bus, cloud_client=self.client)
        self.last_observation = None
        self.last_emotion_state = None
        # Typed per-turn context for prompts and the UI (see brain/emotion_context.py).
        self.emotion_context = ResponseEmotionContext.baseline()

        if not api_key:
            return

        self.language_engine = LanguageEngine(cloud_provider=self)
        self.skills = {
            "get_weather": get_weather,
            "remember_fact": self._remember_fact,
        }

    def _remember_fact(self, key: str, value: str) -> dict:
        key = str(key).strip()
        value = str(value).strip()
        if key.lower() in _IDENTITY_FACT_KEYS and self.current_user.is_guest:
            # A model-inferred name is only a candidate, same as a typed one.
            self._propose_name(value)
            return {"ok": False, "pending_confirmation": True,
                    "message": f"Ask the user to confirm their name is {value} before it is saved."}
        if not can(self.current_user.auth_status, "persist_facts"):
            audit("permission.denied", outcome="denied", actor=actor_of(self.current_user),
                  action="persist_fact", key=key)
            return {"ok": False, "error": "guest speakers cannot save persistent facts; "
                                          "confirm the user's name first"}
        if key.startswith("_"):
            return {"ok": False, "error": "reserved key"}
        if key.lower() == "name":
            # The profile's name mirrors its identity record; renaming who someone
            # *is* goes through the identity flow, not a model tool call.
            return {"ok": False, "error": "the profile name can't be changed by the model"}
        self.current_user.remember_fact(key, value)
        return {"ok": True, "saved": {key: value}}

    def _propose_name(self, name: str) -> None:
        audit("identity.proposed", actor=actor_of(self.current_user), claimed_name=name)
        self.pending_name = name
        self._identity_note = (
            f"The speaker may have introduced themselves as \"{name}\". Before "
            f"treating them as {name}, ask them to confirm in a short, natural way "
            f"(e.g. \"Just to check, should I remember you as {name}?\"). Do not "
            f"assume it's true yet."
        )

    def _set_user(self, display_name: str, auth_status=AuthStatus.CLAIMED,
                  verified_by: str | None = None, via: str = "conversation"):
        previous = actor_of(self.current_user)
        self.current_user = self.memory.get_user(display_name, auth_status, verified_by)
        audit("identity.switched", actor=actor_of(self.current_user), previous=previous, via=via)
        self.pending_name = None
        self._asked_name_this_session = False
        self.last_emotion_state = None
        self.emotion_context = ResponseEmotionContext.baseline()
        self.last_trust = trust_level(self.current_user)

    def protected_methods(self, display_name: str) -> list[str]:
        """Verification methods enrolled on an existing profile ([] = unprotected)."""
        return self.verifiers.enrolled_methods(self.memory.identity_record(display_name))

    def set_user(self, display_name: str, via: str = "settings") -> None:
        """Explicit identity selection (e.g. from the settings panel).

        Yields a *claimed* identity. Raises IdentityVerificationRequired for a
        profile with an enrolled credential; use verify_identity() instead."""
        with self._lock:
            methods = self.protected_methods(display_name)
            if methods and not self._is_verified_as(display_name):
                audit("identity.switch_blocked", outcome="denied",
                      actor=actor_of(self.current_user), target=display_name,
                      via=via, required=methods)
                raise IdentityVerificationRequired(display_name, methods)
            if self._is_verified_as(display_name):
                return  # already verified as this profile; don't downgrade
            self._set_user(display_name, via=via)

    def _is_verified_as(self, display_name: str) -> bool:
        user = self.current_user
        return (user.auth_status is AuthStatus.VERIFIED
                and user.key == display_name.strip().lower())

    def verify_identity(self, display_name: str, method: str, secret: str) -> VerificationResult:
        """Prove control of a profile's credential; on success the session
        becomes that user with auth_status "verified"."""
        with self._lock:
            record = self.memory.identity_record(display_name)
            if record is None:
                result = VerificationResult(False, method, "unknown profile")
            else:
                result = self.verifiers.verify(record["user_id"], record, method, str(secret))
            audit("identity.verify", outcome="ok" if result.ok else "denied",
                  actor=actor_of(self.current_user), target=display_name,
                  target_user_id=(record or {}).get("user_id"), method=method,
                  reason=result.reason or None, retry_after_s=result.retry_after_s)
            if result.ok:
                self._set_user(display_name, AuthStatus.VERIFIED, verified_by=method,
                               via=f"verify:{method}")
            return result

    def sign_out(self) -> None:
        """Drop back to the shared guest identity (conversation state is kept
        with the profile it belongs to)."""
        with self._lock:
            if not self.current_user.is_guest:
                self._set_user(GUEST_KEY, via="sign_out")

    def _resolve_speaker(self, user_text: str):
        """Self-declared, *confirmed* identity only — see class docstring."""
        self._identity_note = None
        candidate = extract_name(user_text)

        if self.pending_name is not None:
            pending = self.pending_name
            answer = parse_confirmation(user_text)
            same = candidate is None or candidate.strip().lower() == pending.strip().lower()
            if answer is True and same:
                methods = self.protected_methods(pending)
                if methods and not self._is_verified_as(pending):
                    # A protected profile can't be entered by saying its name.
                    self.pending_name = None
                    audit("identity.claim_blocked", outcome="denied",
                          actor=actor_of(self.current_user), claimed_name=pending,
                          required=methods)
                    self._identity_note = (
                        f"The profile \"{pending}\" is protected. Do not treat the speaker "
                        f"as {pending} or reveal anything you know about {pending}. Tell "
                        f"them briefly that they need to verify in the Aegis app to use "
                        f"that profile."
                    )
                    return
                self._set_user(pending)
                self._identity_note = (
                    f"The speaker just confirmed they are {self.current_user.display_name}."
                )
                return
            if candidate and not same:
                self._propose_name(candidate)
                return
            self.pending_name = None
            if answer is False:
                audit("identity.rejected", actor=actor_of(self.current_user),
                      claimed_name=pending)
                self._identity_note = (
                    "The speaker said that name was wrong. Don't use it; you may "
                    "ask what you should call them."
                )
            return

        if candidate and candidate.strip().lower() != self.current_user.key:
            self._propose_name(candidate)

    def _save_explicit_facts(self, user_text: str) -> None:
        if not can(self.current_user.auth_status, "persist_facts"):
            return
        for pattern in _EXPLICIT_FACT_PATTERNS:
            match = pattern.search(user_text.strip().rstrip(".!?"))
            if not match:
                continue
            key = re.sub(r"\s+", "_", match.group(1).strip().lower())
            value = match.group(2).strip()
            if key and value and key not in _IDENTITY_FACT_KEYS:
                self.current_user.remember_fact(key, value)
            return

    def reset_conversation(self) -> None:
        """Clear everything that belongs to the current conversation: turns,
        the model's interaction chain, pending identity, and emotional state.
        Durable facts and the current (confirmed) identity are kept."""
        with self._lock:
            user = self.current_user
            audit("conversation.reset", actor=actor_of(user))
            user.clear_conversation()
            self.pending_name = None
            self._identity_note = None
            self._asked_name_this_session = False
            self.emotion_engine.reset([f"user:{user.key}", "aegis"])
            self.event_bus.events(clear=True)
            self.last_observation = None
            self.last_emotion_state = None
            self.last_emotion = "neutral"
            self.emotion_context = ResponseEmotionContext.baseline()
            self.last_trust = trust_level(user)

    def _observe_emotion(self, user_text: str) -> None:
        """Update user perception and AEGIS's regulated state for this turn."""
        trust = trust_level(self.current_user)
        entity = f"user:{self.current_user.key}"
        context = {
            "trust": trust,
            "recent_turns": self.current_user.recent()[-4:],
            "user_key": self.current_user.key,
        }
        observation = self.emotion_engine.process(user_text, entity=entity, context=context)
        state = self.emotion_engine.regulate(observation)
        label = legacy_label(state)
        # Preserve the existing trust rule: don't perform warmth toward
        # someone who hasn't earned it yet.
        if trust == "low" and label == "happy":
            label = "neutral"
        self.last_observation = observation
        self.last_emotion_state = state
        self.last_emotion = label
        self.emotion_context = ResponseEmotionContext.from_turn(
            observation=observation,
            user_state=self.emotion_engine.state(entity),
            aegis_state=state,
            label=label,
            trust=trust,
        )

    def _build_system_instruction(self) -> str:
        trust = trust_level(self.current_user)
        if self.last_emotion_state is None:
            self.last_emotion = compute_emotion(self.current_user, trust)
        self.last_trust = trust
        emotion = self.last_emotion

        system = mind.as_prompt(trust=trust, emotion=emotion)
        system += "\n\n[TOOL USE]\n" + _TOOL_USE_INSTRUCTION

        if self._identity_note:
            system += "\n\n[IDENTITY]\n" + self._identity_note
        elif self.current_user.is_guest and not self._asked_name_this_session:
            system += "\n\n[IDENTITY]\n" + _ASK_NAME_INSTRUCTION
            self._asked_name_this_session = True

        if self.current_user.facts_as_text():
            system += "\n\n" + self.current_user.facts_as_text()
        # Give the response model calibrated state, not a bare sentiment
        # label. This preserves uncertainty and the distinction between the
        # user's evidence and AEGIS's regulated response tendency.
        affective = self.emotion_context.to_prompt()
        if affective:
            system += "\n\n" + affective
        return system

    def generate(self, request: GenerationRequest) -> str:
        context = request.context or {}
        return self._respond_direct(
            request.text,
            context.get("system_prompt"),
            prepared=True,
        )

    def respond(self, user_text: str) -> str:
        with self._lock:
            self._begin_turn(user_text)
            if self.uses_fallback:
                return self._fallback_reply()

            system = self._build_system_instruction()
            result = self.language_engine.reply(
                text=user_text,
                intent=None,
                context={"system_prompt": system},
            )
            self.current_user.add_turn("assistant", result.text)
            return result.text

    def _begin_turn(self, user_text: str) -> None:
        self._resolve_speaker(user_text)
        self.current_user.add_turn("user", user_text)
        self._save_explicit_facts(user_text)
        self._observe_emotion(user_text)

    def _fallback_reply(self) -> str:
        reply = _FALLBACK_REPLY
        if self.pending_name:
            reply = f"Just to check, should I remember you as {self.pending_name}? " + reply
        self.current_user.add_turn("assistant", reply)
        return reply

    def _respond_direct(
        self,
        user_text: str,
        system: Optional[str] = None,
        prepared: bool = False,
    ) -> str:
        with self._lock:
            if not prepared:
                self._begin_turn(user_text)
            if self.uses_fallback:
                return self._fallback_reply()
            return self._call_model(user_text, system or self._build_system_instruction())

    def _call_model(self, user_text: str, system: str) -> str:
        prev_id = self.current_user.get_state(_INTERACTION_KEY)
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
                perm = check_permission(step.name, self.current_user.auth_status)
                if not perm.allowed:
                    audit("permission.denied", outcome="denied",
                          actor=actor_of(self.current_user), skill=step.name,
                          reason=perm.reason)
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
        self.current_user.set_state(_INTERACTION_KEY, interaction.id)
        return final_text
