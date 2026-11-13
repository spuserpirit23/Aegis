"""Identity model and verification interfaces.

Three things that used to be blurred together are kept separate here:

* ``user_id`` — an opaque, stable internal ID (``usr_<hex>``) minted when a
  profile is created. Never derived from, or changed by, the display name.
* ``display_name`` — what the person is called. Self-declared, cosmetic.
* ``auth_status`` — how much we actually know about *this session's* speaker:

  - ``guest``    unidentified; shared, non-persistent bucket
  - ``claimed``  said a name and confirmed it in conversation; NOT verified
  - ``verified`` proved control of a credential enrolled on the profile

Auth status is per session and never persisted: a restart drops everyone back
to guest. Trust (brain/trust.py) caps ``claimed`` at MEDIUM so saying a name
can never buy HIGH trust, and profiles with an enrolled credential cannot be
entered by claiming their name at all (see Planner._resolve_speaker).

Verification is pluggable via :class:`IdentityVerifier`. A local PIN verifier
ships now; voice/face verifiers can implement the same protocol later.

Enrol a PIN (with Aegis stopped, since the running app owns memory.json)::

    python -m brain.identity enroll-pin --user Spirit
    python -m brain.identity remove-credentials --user Spirit
    python -m brain.identity show --user Spirit
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class AuthStatus(str, Enum):
    GUEST = "guest"
    CLAIMED = "claimed"
    VERIFIED = "verified"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def __str__(self) -> str:  # json/f-strings show "claimed", not "AuthStatus.CLAIMED"
        return self.value


_RANK = {AuthStatus.GUEST: 0, AuthStatus.CLAIMED: 1, AuthStatus.VERIFIED: 2}


def coerce_status(value) -> AuthStatus:
    """Map anything (enum, string, None) onto an AuthStatus. Unknown -> CLAIMED,
    the safest status for a named profile: never guest-shared, never high."""
    if isinstance(value, AuthStatus):
        return value
    try:
        return AuthStatus(str(value).lower())
    except ValueError:
        return AuthStatus.CLAIMED


def new_user_id() -> str:
    return "usr_" + secrets.token_hex(8)


@dataclass(frozen=True)
class Identity:
    user_id: str
    display_name: str
    auth_status: AuthStatus
    verified_by: str | None = None
    verified_at: float | None = None

    @property
    def is_guest(self) -> bool:
        return self.auth_status is AuthStatus.GUEST

    @property
    def is_verified(self) -> bool:
        return self.auth_status is AuthStatus.VERIFIED

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "auth_status": self.auth_status.value,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at,
        }


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    method: str
    reason: str = ""
    retry_after_s: float | None = None


class IdentityVerificationRequired(PermissionError):
    """Raised when an action needs a verified identity for that profile."""

    def __init__(self, display_name: str, methods: list[str]):
        super().__init__(f"profile '{display_name}' requires verification ({', '.join(methods)})")
        self.display_name = display_name
        self.methods = methods


# ------------------------------------------------------------------ verifiers

@runtime_checkable
class IdentityVerifier(Protocol):
    """One way of proving you are a given profile.

    ``record`` is the profile's persisted identity dict (see brain/memory.py);
    verifiers keep their material under ``record["credentials"][method]``.
    Implementations must not store secrets in plain text.
    """

    method: str

    def is_enrolled(self, record: dict) -> bool: ...

    def enroll(self, record: dict, secret: str) -> None: ...

    def verify(self, record: dict, secret: str) -> VerificationResult: ...


class PinVerifier:
    """Local PIN/passphrase, stored as salted PBKDF2-SHA256."""

    method = "pin"
    iterations = 200_000
    min_length = 4

    def is_enrolled(self, record: dict) -> bool:
        cred = (record or {}).get("credentials", {}).get(self.method)
        return bool(cred and cred.get("hash") and cred.get("salt"))

    def _derive(self, secret: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)

    def enroll(self, record: dict, secret: str) -> None:
        if len(secret) < self.min_length:
            raise ValueError(f"PIN must be at least {self.min_length} characters")
        salt = secrets.token_bytes(16)
        record.setdefault("credentials", {})[self.method] = {
            "salt": salt.hex(),
            "hash": self._derive(secret, salt, self.iterations).hex(),
            "iterations": self.iterations,
            "enrolled_at": time.time(),
        }

    def verify(self, record: dict, secret: str) -> VerificationResult:
        if not self.is_enrolled(record):
            return VerificationResult(False, self.method, "no PIN enrolled for this profile")
        cred = record["credentials"][self.method]
        try:
            salt = bytes.fromhex(cred["salt"])
            expected = bytes.fromhex(cred["hash"])
            iterations = int(cred.get("iterations", self.iterations))
        except (KeyError, ValueError, TypeError):
            return VerificationResult(False, self.method, "stored PIN record is corrupt")
        actual = self._derive(str(secret), salt, iterations)
        if hmac.compare_digest(actual, expected):
            return VerificationResult(True, self.method)
        return VerificationResult(False, self.method, "incorrect PIN")


class VerifierRegistry:
    """Holds the available verifiers and rate-limits failed attempts.

    After ``max_failures`` consecutive failures for a profile, further attempts
    are refused for ``lockout_s`` seconds (in memory; resets on restart).
    """

    def __init__(self, verifiers: list[IdentityVerifier] | None = None,
                 max_failures: int = 5, lockout_s: float = 300.0, clock=time.monotonic):
        self._verifiers: dict[str, IdentityVerifier] = {}
        self.max_failures = max_failures
        self.lockout_s = lockout_s
        self._clock = clock
        self._failures: dict[str, tuple[int, float]] = {}  # user_id -> (count, locked_until)
        self._lock = threading.Lock()
        for verifier in verifiers or []:
            self.register(verifier)

    def register(self, verifier: IdentityVerifier) -> None:
        self._verifiers[verifier.method] = verifier

    def get(self, method: str) -> IdentityVerifier | None:
        return self._verifiers.get(method)

    def methods(self) -> list[str]:
        return sorted(self._verifiers)

    def enrolled_methods(self, record: dict | None) -> list[str]:
        if not record:
            return []
        return sorted(m for m, v in self._verifiers.items() if v.is_enrolled(record))

    def is_protected(self, record: dict | None) -> bool:
        return bool(self.enrolled_methods(record))

    def verify(self, user_id: str, record: dict, method: str, secret: str) -> VerificationResult:
        with self._lock:
            count, locked_until = self._failures.get(user_id, (0, 0.0))
            now = self._clock()
            if locked_until > now:
                return VerificationResult(False, method, "too many failed attempts",
                                          retry_after_s=round(locked_until - now, 1))
        verifier = self.get(method)
        if verifier is None:
            return VerificationResult(False, method, f"unknown verification method '{method}'")
        result = verifier.verify(record, secret)
        with self._lock:
            if result.ok:
                self._failures.pop(user_id, None)
            elif verifier.is_enrolled(record):
                count += 1
                locked_until = self._clock() + self.lockout_s if count >= self.max_failures else 0.0
                self._failures[user_id] = (0 if locked_until else count, locked_until)
        return result


def default_registry() -> VerifierRegistry:
    return VerifierRegistry([PinVerifier()])


# ------------------------------------------------------------------------ CLI

def _bridge_running(host: str = "127.0.0.1", port: int = 8765) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    import getpass
    import json

    from brain.audit import audit
    from brain.memory import Memory

    parser = argparse.ArgumentParser(prog="python -m brain.identity",
                                     description="Manage Aegis identity credentials.")
    parser.add_argument("--path", default="memory.json")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("enroll-pin", "remove-credentials", "show"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--user", required=True, help="existing profile display name")
    args = parser.parse_args(argv)

    if args.command != "show" and _bridge_running():
        print("Aegis appears to be running (port 8765 is open). Stop it first: the running "
              "app owns memory.json and would overwrite this change on its next save.")
        return 2

    memory = Memory(path=args.path)
    record = memory.identity_record(args.user)
    if record is None:
        print(f"No profile named '{args.user}'. Known: {', '.join(memory.known_users()) or '(none)'}")
        return 1
    registry = default_registry()

    if args.command == "show":
        print(json.dumps({"user_id": record.get("user_id"),
                          "display_name": record.get("display_name"),
                          "enrolled_methods": registry.enrolled_methods(record)}, indent=2))
        return 0

    if args.command == "remove-credentials":
        record["credentials"] = {}
        memory.save()
        audit("identity.credentials_removed", actor=None, user_id=record.get("user_id"),
              display_name=record.get("display_name"), via="cli")
        print(f"Removed all credentials from '{args.user}'.")
        return 0

    pin = getpass.getpass("New PIN: ")
    if pin != getpass.getpass("Repeat PIN: "):
        print("PINs did not match.")
        return 1
    try:
        registry.get("pin").enroll(record, pin)
    except ValueError as exc:
        print(exc)
        return 1
    memory.save()
    audit("identity.credential_enrolled", actor=None, user_id=record.get("user_id"),
          display_name=record.get("display_name"), method="pin", via="cli")
    print(f"PIN enrolled for '{args.user}'. This profile can no longer be entered by "
          "claiming its name; verify in the app to unlock it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
