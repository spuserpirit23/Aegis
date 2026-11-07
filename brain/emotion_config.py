"""Emotion engine tuning loaded from ``mind/emotion.yaml``.

Precedence, highest first: environment variables, the YAML file, built-in
defaults. Loading never raises: a missing file means defaults, and an invalid
or unknown key is logged and ignored so a config typo can't stop AEGIS from
starting.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger(__name__)

CONFIG_ENV = "AEGIS_EMOTION_CONFIG"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "mind" / "emotion.yaml"

# Environment variable -> top-level config key.
_ENV_OVERRIDES = {
    "AEGIS_EMOTION_PROVIDER": "provider",
    "AEGIS_EMOTION_LOCAL_MODEL": "local_model",
    "AEGIS_EMOTION_CLOUD_MODEL": "cloud_model",
}


def _unit(value: Any) -> float:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError("must be between 0 and 1")
    return value


def _positive(value: Any) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("must be a positive number")
    return value


def _count(value: Any) -> int:
    if isinstance(value, bool) or int(value) != float(value) or int(value) < 1:
        raise ValueError("must be a whole number of at least 1")
    return int(value)


def _flag(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class EngineTuning:
    persistence: float = .72
    decay: bool = True
    dimension_half_life_seconds: float = 900.0
    history_limit: int = 100
    event_limit: int = 500
    change_threshold: float = .03


@dataclass(frozen=True)
class RegulationTuning:
    concern_base: float = .30
    concern_gain: float = .45
    calm_base: float = .54
    calm_under_stress: float = .54


_VALIDATORS = {
    "persistence": _unit, "decay": _flag, "dimension_half_life_seconds": _positive,
    "history_limit": _count, "event_limit": _count, "change_threshold": _unit,
    "concern_base": _unit, "concern_gain": _unit, "calm_base": _unit, "calm_under_stress": _unit,
    "provider": _text, "local_model": _text, "cloud_model": _text,
}


@dataclass(frozen=True)
class EmotionConfig:
    provider: str = "rule"
    local_model: str = "j-hartmann/emotion-english-distilroberta-base"
    cloud_model: str = "gemini-3.6-flash"
    engine: EngineTuning = field(default_factory=EngineTuning)
    regulation: RegulationTuning = field(default_factory=RegulationTuning)
    half_lives: Mapping[str, float] = field(default_factory=dict)
    source: str = "defaults"  # file the values came from, for diagnostics

    def engine_kwargs(self) -> dict[str, Any]:
        return {f.name: getattr(self.engine, f.name) for f in fields(self.engine)}

    def regulation_kwargs(self) -> dict[str, float]:
        return {f.name: getattr(self.regulation, f.name) for f in fields(self.regulation)}


def _validated(section: str, raw: Any, cls) -> Any:
    """Build a tuning dataclass from a YAML mapping, keeping defaults for bad keys."""
    if raw is None:
        return cls()
    if not isinstance(raw, Mapping):
        log.warning("emotion config: %r must be a mapping; using defaults", section)
        return cls()
    allowed = {f.name for f in fields(cls)}
    values = {}
    for key, value in raw.items():
        if key not in allowed:
            log.warning("emotion config: unknown key %s.%s (allowed: %s)",
                        section, key, ", ".join(sorted(allowed)))
            continue
        try:
            values[key] = _VALIDATORS[key](value)
        except (TypeError, ValueError) as exc:
            log.warning("emotion config: %s.%s=%r %s; using default", section, key, value, exc)
    return cls(**values)


def _half_lives(raw: Any) -> dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        log.warning("emotion config: 'half_lives' must be a mapping; ignoring it")
        return {}
    result = {}
    for name, value in raw.items():
        try:
            result[_text(name).lower()] = _positive(value)
        except (TypeError, ValueError) as exc:
            log.warning("emotion config: half_lives.%s=%r %s; ignoring it", name, value, exc)
    return result


def _read_yaml(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # unreadable file or invalid YAML
        log.warning("emotion config: could not read %s (%s); using defaults", path, exc)
        return {}
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        log.warning("emotion config: %s must contain a mapping; using defaults", path)
        return {}
    return data


def load_emotion_config(path: str | os.PathLike | None = None,
                        env: Mapping[str, str] | None = None) -> EmotionConfig:
    env = os.environ if env is None else env
    config_path = Path(path or env.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH)
    data = dict(_read_yaml(config_path))

    known = {"provider", "local_model", "cloud_model", "engine", "regulation", "half_lives"}
    for key in set(data) - known:
        log.warning("emotion config: unknown top-level key %r (allowed: %s)", key, ", ".join(sorted(known)))

    top: dict[str, str] = {}
    for key in ("provider", "local_model", "cloud_model"):
        if key in data:
            try:
                top[key] = _text(data[key])
            except ValueError as exc:
                log.warning("emotion config: %s=%r %s; using default", key, data[key], exc)
    for env_key, key in _ENV_OVERRIDES.items():
        if (env.get(env_key) or "").strip():
            top[key] = env[env_key].strip()

    return EmotionConfig(
        **top,
        engine=_validated("engine", data.get("engine"), EngineTuning),
        regulation=_validated("regulation", data.get("regulation"), RegulationTuning),
        half_lives=_half_lives(data.get("half_lives")),
        source=str(config_path) if config_path.exists() else "defaults",
    )
