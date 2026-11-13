"""Permissions: what each identity tier may do, enforced at the point of use.

Trust (brain/trust.py) shapes *tone*; permission decides *actions*. They are
deliberately separate: high trust never unlocks a skill on its own, and a
skill's minimum tier is checked every time it runs.

Tiers come from brain/identity.py: guest < claimed < verified.
Unknown skills are denied. Unknown statuses are treated as guest.
"""
from __future__ import annotations

from dataclasses import dataclass

from brain.identity import AuthStatus


@dataclass(frozen=True)
class Permission:
    allowed: bool
    reason: str
    requires_confirmation: bool = False
    required_status: str | None = None


# Minimum identity tier per skill. remember_fact is open to guests only so a
# guest's introduction can be routed into the name-confirmation flow; the
# skill itself refuses to persist anything for a guest (see GUEST_POLICY).
SKILL_MIN_STATUS: dict[str, AuthStatus] = {
    "get_weather": AuthStatus.GUEST,
    "remember_fact": AuthStatus.GUEST,
}

# Non-skill capabilities, by minimum tier.
CAPABILITY_MIN_STATUS: dict[str, AuthStatus] = {
    "converse": AuthStatus.GUEST,
    "propose_identity": AuthStatus.GUEST,
    "persist_facts": AuthStatus.CLAIMED,
    "persist_history": AuthStatus.CLAIMED,
    "view_own_facts": AuthStatus.CLAIMED,
    "high_trust": AuthStatus.VERIFIED,
    "enter_protected_profile": AuthStatus.VERIFIED,
}

# Human-readable statement of guest rights, surfaced by the bridge/UI.
GUEST_POLICY = {
    "can": ["converse", "use get_weather", "introduce themselves (name needs confirmation)"],
    "cannot": [
        "save facts or history (guest memory is shared and never written to disk)",
        "rise above LOW trust",
        "enter a profile protected by a credential",
    ],
}


def _status(auth_status) -> AuthStatus:
    if isinstance(auth_status, AuthStatus):
        return auth_status
    try:
        return AuthStatus(str(auth_status).lower())
    except ValueError:
        return AuthStatus.GUEST


def allowed_skills(auth_status=AuthStatus.VERIFIED) -> list[str]:
    """Skills usable at ``auth_status`` (default: everything that is enabled)."""
    status = _status(auth_status)
    return sorted(name for name, need in SKILL_MIN_STATUS.items() if status.rank >= need.rank)


def can(auth_status, capability: str) -> bool:
    need = CAPABILITY_MIN_STATUS.get(capability)
    return need is not None and _status(auth_status).rank >= need.rank


def check_permission(skill_name: str, auth_status=AuthStatus.GUEST) -> Permission:
    need = SKILL_MIN_STATUS.get(skill_name)
    if need is None:
        return Permission(
            False,
            f"Skill '{skill_name}' is not enabled yet.",
            requires_confirmation=True,
        )
    status = _status(auth_status)
    if status.rank < need.rank:
        return Permission(
            False,
            f"Skill '{skill_name}' requires a {need.value} identity (speaker is {status.value}).",
            required_status=need.value,
        )
    return Permission(True, "Skill is allowed.", required_status=need.value)


def policy_table() -> dict:
    tiers = list(AuthStatus)
    return {
        tier.value: {
            "skills": allowed_skills(tier),
            "capabilities": sorted(c for c in CAPABILITY_MIN_STATUS if can(tier, c)),
        }
        for tier in tiers
    }
