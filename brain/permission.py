from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    allowed: bool
    reason: str
    requires_confirmation: bool = False


_ALLOWED_SKILLS = {"get_weather", "remember_fact"}


def check_permission(skill_name: str) -> Permission:
    if skill_name in _ALLOWED_SKILLS:
        return Permission(True, "Skill is allowed.")
    return Permission(
        False,
        f"Skill '{skill_name}' is not enabled yet.",
        requires_confirmation=True,
    )