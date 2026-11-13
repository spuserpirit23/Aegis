"""Local bridge authentication.

The bridge listens on loopback only, but loopback is not a trust boundary on
its own: any local process, and any web page open in a browser (via fetch or
DNS rebinding), can reach 127.0.0.1. So every request must carry a per-launch
bearer token, and browser-originated requests are refused unless their Origin
is explicitly allowed.

Token source, in order:
  1. ``AEGIS_BRIDGE_TOKEN`` environment variable (main.py passes it to Godot)
  2. otherwise a fresh random token, written to ``.aegis/bridge_token`` so a
     Godot editor opened by hand can read it

Allowed browser origins (default: none): ``AEGIS_BRIDGE_ALLOWED_ORIGINS``,
comma-separated, e.g. ``http://localhost:5173``.
"""
from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOKEN_ENV = "AEGIS_BRIDGE_TOKEN"
ORIGINS_ENV = "AEGIS_BRIDGE_ALLOWED_ORIGINS"
DEFAULT_TOKEN_FILE = _PROJECT_ROOT / ".aegis" / "bridge_token"


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def write_token_file(token: str, path: Path = DEFAULT_TOKEN_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # best effort; effectively a no-op on Windows
    except OSError:
        pass
    tmp.replace(path)
    return path


def read_token_file(path: Path = DEFAULT_TOKEN_FILE) -> str | None:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def resolve_token(token_file: Path | None = DEFAULT_TOKEN_FILE) -> str:
    """Return the token this bridge instance will require."""
    token = os.environ.get(TOKEN_ENV, "").strip() or generate_token()
    if token_file is not None:
        write_token_file(token, token_file)
    return token


def allowed_origins() -> set[str]:
    raw = os.environ.get(ORIGINS_ENV, "")
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def token_matches(expected: str, authorization_header: str | None) -> bool:
    if not expected or not authorization_header:
        return False
    scheme, _, presented = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.strip().encode(), expected.encode())


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_header_allowed(host_header: str | None, port: int) -> bool:
    """Reject DNS-rebinding requests whose Host isn't a loopback name/IP."""
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):  # [::1]:8765
        name, _, rest = host[1:].partition("]")
        port_part = rest[1:] if rest.startswith(":") else ""
    else:
        name, _, port_part = host.partition(":")
    if port_part and port_part != str(port):
        return False
    return is_loopback_host(name)
