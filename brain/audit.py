"""Append-only audit log for security-relevant events.

One JSON object per line (JSONL) so the file is greppable and trivially
parseable. Records *who did what, and whether it was allowed*: bridge auth
failures, identity claims/switches/verifications, settings changes, resets,
permission denials. It never records conversation content or secrets — any
detail key that looks like a credential is redacted before writing.

Location: ``AEGIS_AUDIT_LOG`` if set, else ``logs/audit.jsonl`` under the
project root. Rotates at ~1 MB, keeping 3 old files.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _PROJECT_ROOT / "logs" / "audit.jsonl"

_SECRET_MARKERS = ("secret", "pin", "password", "token", "credential", "hash", "salt")
_REDACTED = "[redacted]"


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return _REDACTED
    if isinstance(value, dict):
        return {k: _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def actor_of(user) -> dict | None:
    """Describe a UserMemory view (or None) for the ``actor`` field."""
    if user is None:
        return None
    return {
        "user_id": getattr(user, "user_id", None),
        "display_name": getattr(user, "display_name", None),
        "auth_status": str(getattr(user, "auth_status", "unknown")),
    }


class AuditLog:
    def __init__(self, path: str | os.PathLike | None = None,
                 max_bytes: int = 1_000_000, backups: int = 3):
        self.path = Path(path) if path else _DEFAULT_PATH
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()

    def record(self, event: str, *, outcome: str = "ok", actor: dict | None = None,
               **details: Any) -> dict:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            "outcome": outcome,
            "actor": actor,
            "details": _redact(details),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed(len(line) + 1)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                # Auditing must never take down a conversation turn.
                pass
        return entry

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size + incoming <= self.max_bytes:
            return
        for index in range(self.backups - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{index}")
            if src.exists():
                src.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))

    def tail(self, limit: int = 50) -> list[dict]:
        with self._lock:
            if not self.path.exists():
                return []
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                lines = deque(fh, maxlen=max(0, limit))
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries


_log_lock = threading.Lock()
_log: AuditLog | None = None


def get_audit_log() -> AuditLog:
    global _log
    with _log_lock:
        if _log is None:
            _log = AuditLog(os.environ.get("AEGIS_AUDIT_LOG") or None)
        return _log


def set_audit_log(log: AuditLog | None) -> None:
    """Swap the process-wide log (tests point it at a temp file)."""
    global _log
    with _log_lock:
        _log = log


def audit(event: str, **kwargs: Any) -> dict:
    return get_audit_log().record(event, **kwargs)
