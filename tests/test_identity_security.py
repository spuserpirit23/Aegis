"""Plan 2 — identity and security domain.

Covers: the explicit identity model (user_id / display_name / auth_status),
verification interfaces, the trust ceiling for self-declared names, tiered
permissions, audit logging, and the authenticated bridge (no wildcard CORS,
Host/Origin checks, protected endpoints).
"""
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

from brain import bridge_auth
from brain.identity import (AuthStatus, IdentityVerificationRequired, IdentityVerifier,
                            PinVerifier, VerifierRegistry)
from brain.memory import GUEST_KEY, Memory
from brain.permission import (GUEST_POLICY, allowed_skills, can, check_permission,
                              policy_table)
from brain.trust import trust_breakdown, trust_level


class FastPin(PinVerifier):
    iterations = 1_000  # keep tests fast; production uses 200k


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def path():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as handle:
        name = handle.name
    os.remove(name)
    yield name
    for p in (name, name + ".tmp"):
        if os.path.exists(p):
            os.remove(p)


def offline_planner(path, registry=None):
    from brain.planner import Planner

    with patch("brain.planner.load_env_file", return_value={}), \
            patch.dict(os.environ, {"AEGIS_EMOTION_PROVIDER": "rule"}):
        os.environ.pop("GEMINI_API_KEY", None)
        return Planner(Memory(path=path), verifiers=registry or VerifierRegistry([FastPin()]))


def protect(planner, name, pin="2468"):
    planner.memory.get_user(name)
    record = planner.memory.identity_record(name)
    planner.verifiers.get("pin").enroll(record, pin)
    planner.memory.save()
    return record


# ------------------------------------------------------------ identity model

def test_profiles_get_stable_internal_ids_separate_from_names(path):
    mem = Memory(path=path)
    sam = mem.get_user("Sam")
    assert sam.user_id.startswith("usr_") and "sam" not in sam.user_id
    assert sam.display_name == "Sam" and sam.key == "sam"
    again = Memory(path=path).get_user("SAM")
    assert again.user_id == sam.user_id  # survives reload and spelling


def test_guest_identity(path):
    guest = Memory(path=path).get_user(GUEST_KEY)
    ident = guest.identity
    assert ident.auth_status is AuthStatus.GUEST and ident.user_id == GUEST_KEY
    assert ident.to_dict()["auth_status"] == "guest"


def test_auth_status_is_session_only_never_persisted(path):
    mem = Memory(path=path)
    verified = mem.get_user("Sam", AuthStatus.VERIFIED, verified_by="pin")
    assert verified.auth_status is AuthStatus.VERIFIED and verified.verified_by == "pin"
    raw = json.loads(open(path, encoding="utf-8").read())
    assert "verified" not in json.dumps(raw["users"]["sam"])
    assert Memory(path=path).get_user("Sam").auth_status is AuthStatus.CLAIMED


def test_named_profile_can_never_be_marked_guest(path):
    assert Memory(path=path).get_user("Sam", "guest").auth_status is AuthStatus.CLAIMED


def test_legacy_buckets_are_migrated_with_an_identity_record(path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"users": {"spirit": {"turns": [], "facts": {"name": "Spirit"}}},
                   "last_user": None}, fh)
    mem = Memory(path=path)
    record = mem.identity_record("spirit")
    assert record["user_id"].startswith("usr_")
    assert record["display_name"] == "Spirit" and record["credentials"] == {}
    persisted = json.loads(open(path, encoding="utf-8").read())
    assert persisted["users"]["spirit"]["identity"]["user_id"] == record["user_id"]


def test_credentials_never_reach_facts_or_model_prompt(path):
    planner = offline_planner(path)
    protect(planner, "Sam")
    user = planner.memory.peek_user("Sam")
    assert "credentials" not in json.dumps(user.facts)
    assert "salt" not in user.facts_as_text() and "hash" not in user.facts_as_text()


# ------------------------------------------------------------- verification

def test_pin_verifier_hashes_and_verifies():
    record = {}
    pin = FastPin()
    assert isinstance(pin, IdentityVerifier)
    pin.enroll(record, "2468")
    assert "2468" not in json.dumps(record)
    assert pin.verify(record, "2468").ok
    bad = pin.verify(record, "1111")
    assert not bad.ok and bad.reason == "incorrect PIN"
    with pytest.raises(ValueError):
        pin.enroll({}, "12")


def test_registry_locks_out_after_repeated_failures():
    clock = FakeClock()
    registry = VerifierRegistry([FastPin()], max_failures=3, lockout_s=60, clock=clock)
    record = {}
    registry.get("pin").enroll(record, "2468")
    for _ in range(3):
        assert not registry.verify("usr_1", record, "pin", "0000").ok
    locked = registry.verify("usr_1", record, "pin", "2468")  # right PIN, still locked
    assert not locked.ok and locked.retry_after_s
    clock.now += 61
    assert registry.verify("usr_1", record, "pin", "2468").ok


def test_unknown_method_is_refused():
    registry = VerifierRegistry([FastPin()])
    assert not registry.verify("u", {}, "faceprint", "x").ok


# ------------------------------------------------------------- trust ceiling

def test_self_declared_name_cannot_reach_high_trust(path):
    planner = offline_planner(path)
    planner.respond("my name is Sam")
    planner.respond("yes")
    assert planner.current_user.auth_status is AuthStatus.CLAIMED
    for _ in range(40):
        planner.current_user.add_turn("user", "hi")
    t = trust_breakdown(planner.current_user)
    assert t.score == 7 and t.level == "medium" and t.capped
    assert t.percent <= 71  # gauge never looks HIGH
    assert "not verified" in t.reason


def test_verified_identity_can_reach_high_trust(path):
    planner = offline_planner(path)
    protect(planner, "Sam")
    assert planner.verify_identity("Sam", "pin", "2468").ok
    for _ in range(40):
        planner.current_user.add_turn("user", "hi")
    assert trust_level(planner.current_user) == "high"


# ------------------------------------------------------- protected profiles

def test_claiming_a_protected_name_in_conversation_is_blocked(path, isolated_audit_log):
    planner = offline_planner(path)
    protect(planner, "Sam")
    planner.memory.peek_user("Sam").remember_fact("project", "secret rover")
    planner.respond("my name is Sam")
    planner.respond("yes")
    assert planner.current_user.is_guest
    assert planner.pending_name is None
    assert "protected" in (planner._identity_note or "")
    assert "secret rover" not in planner._build_system_instruction()
    events = [e["event"] for e in isolated_audit_log.tail()]
    assert "identity.claim_blocked" in events


def test_settings_switch_to_protected_profile_requires_verification(path):
    planner = offline_planner(path)
    protect(planner, "Sam")
    with pytest.raises(IdentityVerificationRequired) as exc:
        planner.set_user("Sam")
    assert exc.value.methods == ["pin"]
    assert planner.current_user.is_guest


def test_verify_identity_success_failure_and_sign_out(path):
    planner = offline_planner(path)
    protect(planner, "Sam")
    assert not planner.verify_identity("Sam", "pin", "0000").ok
    assert planner.current_user.is_guest
    assert not planner.verify_identity("Nobody", "pin", "2468").ok
    assert planner.verify_identity("Sam", "pin", "2468").ok
    assert planner.current_user.auth_status is AuthStatus.VERIFIED
    planner.set_user("Sam")  # already verified as Sam: no downgrade, no error
    assert planner.current_user.auth_status is AuthStatus.VERIFIED
    planner.sign_out()
    assert planner.current_user.is_guest


def test_unprotected_profiles_still_work_by_claim(path):
    planner = offline_planner(path)
    planner.set_user("Parth")
    assert planner.current_user.key == "parth"
    assert planner.current_user.auth_status is AuthStatus.CLAIMED


def test_model_cannot_rename_a_profile(path):
    planner = offline_planner(path)
    planner.set_user("Sam")
    result = planner._remember_fact("name", "Mallory")
    assert result["ok"] is False
    assert planner.current_user.facts["name"] == "Sam"
    assert planner._remember_fact("nickname", "Sammy")["ok"] is True


# ---------------------------------------------------------------- permission

def test_guest_permissions_are_explicit():
    assert allowed_skills("guest") == ["get_weather", "remember_fact"]
    assert not can("guest", "persist_facts")
    assert not can("guest", "high_trust")
    assert can("claimed", "persist_facts") and not can("claimed", "high_trust")
    assert can("verified", "high_trust")
    assert GUEST_POLICY["cannot"]
    assert set(policy_table()) == {"guest", "claimed", "verified"}


def test_permission_checks_tier_and_denies_unknown():
    assert check_permission("get_weather", "guest").allowed
    assert not check_permission("unlock_door", "verified").allowed
    # An unrecognised status is treated as guest, never as something higher.
    assert allowed_skills("nonsense-status") == allowed_skills("guest")


def test_skill_tier_enforced(monkeypatch):
    from brain import permission

    monkeypatch.setitem(permission.SKILL_MIN_STATUS, "send_email", AuthStatus.VERIFIED)
    denied = check_permission("send_email", AuthStatus.CLAIMED)
    assert not denied.allowed and denied.required_status == "verified"
    assert check_permission("send_email", AuthStatus.VERIFIED).allowed
    assert "send_email" not in allowed_skills("claimed")


# --------------------------------------------------------------------- audit

def test_audit_redacts_secrets_and_records_identity_events(path, isolated_audit_log):
    planner = offline_planner(path)
    protect(planner, "Sam")
    planner.verify_identity("Sam", "pin", "0000")
    planner.verify_identity("Sam", "pin", "2468")
    isolated_audit_log.record("test.secret", pin="2468", nested={"token": "abc"})
    text = isolated_audit_log.path.read_text(encoding="utf-8")
    assert "2468" not in text and "abc" not in text
    entries = isolated_audit_log.tail()
    verifies = [e for e in entries if e["event"] == "identity.verify"]
    assert [e["outcome"] for e in verifies] == ["denied", "ok"]
    switched = [e for e in entries if e["event"] == "identity.switched"][-1]
    assert switched["actor"]["auth_status"] == "verified"
    assert switched["actor"]["user_id"].startswith("usr_")


def test_audit_log_rotates(tmp_path):
    from brain.audit import AuditLog

    log = AuditLog(tmp_path / "a.jsonl", max_bytes=400, backups=2)
    for i in range(30):
        log.record("tick", n=i)
    assert (tmp_path / "a.jsonl.1").exists()
    assert log.path.stat().st_size <= 400


# ---------------------------------------------------------- bridge auth unit

@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1:8765", True), ("localhost:8765", True), ("[::1]:8765", True),
    ("127.0.0.1", True), ("evil.example:8765", False), ("127.0.0.1:9999", False),
    ("", False), (None, False),
])
def test_host_header_allowed(host, ok):
    assert bridge_auth.host_header_allowed(host, 8765) is ok


def test_token_matching():
    assert bridge_auth.token_matches("abc", "Bearer abc")
    assert not bridge_auth.token_matches("abc", "Bearer abd")
    assert not bridge_auth.token_matches("abc", "abc")
    assert not bridge_auth.token_matches("abc", None)


def test_resolve_token_prefers_env_and_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge_auth.TOKEN_ENV, "from-env")
    target = tmp_path / "tok"
    assert bridge_auth.resolve_token(target) == "from-env"
    assert bridge_auth.read_token_file(target) == "from-env"
    monkeypatch.delenv(bridge_auth.TOKEN_ENV)
    fresh = bridge_auth.resolve_token(None)
    assert len(fresh) >= 32 and fresh != "from-env"


def test_create_server_refuses_non_loopback(path):
    from aegis_bridge import create_server

    with pytest.raises(ValueError):
        create_server(offline_planner(path), host="0.0.0.0", port=0, token="t", token_file=None)


# ---------------------------------------------------------- bridge over HTTP

TOKEN = "test-token-123"


@pytest.fixture
def server(path):
    from aegis_bridge import create_server

    planner = offline_planner(path)
    srv = create_server(planner, port=0, token=TOKEN, token_file=None)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, route, body=None, token=TOKEN, headers=None):
    port = srv.server_address[1]
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        raw = err.read()
        return err.code, dict(err.headers), (json.loads(raw) if raw else {})


@pytest.mark.parametrize("method,route", [
    ("GET", "/state"), ("GET", "/memory"), ("GET", "/settings"), ("GET", "/tools"),
    ("GET", "/trust"), ("GET", "/history"), ("GET", "/identity"), ("GET", "/audit"),
    ("POST", "/settings"), ("POST", "/reset"), ("POST", "/respond"),
    ("POST", "/identity/verify"), ("POST", "/identity/sign_out"),
])
@pytest.mark.parametrize("token", [None, "wrong"])
def test_endpoints_require_the_bridge_token(server, method, route, token):
    status, _, body = call(server, method, route, body={} if method == "POST" else None,
                           token=token)
    assert status == 401 and "token" in body["error"]


def test_authorized_requests_succeed_without_wildcard_cors(server):
    status, headers, body = call(server, "GET", "/memory")
    assert status == 200 and body["auth_status"] == "guest"
    assert "Access-Control-Allow-Origin" not in headers
    assert headers.get("Cache-Control") == "no-store"


def test_browser_origins_are_rejected_even_with_token(server):
    status, headers, _ = call(server, "GET", "/memory", headers={"Origin": "https://evil.example"})
    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers


def test_preflight_is_refused_for_unlisted_origins(server):
    status, headers, _ = call(server, "OPTIONS", "/reset", token=None,
                              headers={"Origin": "https://evil.example"})
    assert status == 403 and "Access-Control-Allow-Origin" not in headers


def test_allowed_origin_gets_exact_cors_echo(server):
    server.bridge.allowed_origins = {"http://localhost:5173"}
    status, headers, _ = call(server, "GET", "/state", headers={"Origin": "http://localhost:5173"})
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == "http://localhost:5173"


def test_dns_rebinding_host_is_rejected(server):
    status, _, body = call(server, "GET", "/state", headers={"Host": "attacker.example"})
    assert status == 403 and body["error"] == "host not allowed"


def test_oversized_body_rejected_without_reading_it(server):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.putrequest("POST", "/settings")
    conn.putheader("Authorization", f"Bearer {TOKEN}")
    conn.putheader("Content-Length", "70000")
    conn.endheaders()  # body never sent: the server must answer from headers alone
    assert conn.getresponse().status == 413
    conn.close()


def test_settings_cannot_switch_into_protected_profile(server):
    protect(server.bridge.planner, "Sam")
    status, _, body = call(server, "POST", "/settings", body={"user": "Sam"})
    assert status == 403 and body["methods"] == ["pin"]
    status, _, body = call(server, "POST", "/settings", body={"user": "Parth"})
    assert status == 200 and body["settings"]["active_user"] == "Parth"


def test_verify_and_sign_out_over_http(server):
    protect(server.bridge.planner, "Sam")
    status, _, body = call(server, "POST", "/identity/verify",
                           body={"user": "Sam", "method": "pin", "secret": "0000"})
    assert status == 403 and body["verified"] is False
    status, _, body = call(server, "POST", "/identity/verify",
                           body={"user": "Sam", "method": "pin", "secret": "2468"})
    assert status == 200 and body["identity"]["auth_status"] == "verified"
    _, _, state = call(server, "GET", "/state")
    assert state["auth_status"] == "verified" and state["user"] == "Sam"
    _, _, ident = call(server, "GET", "/identity")
    assert ident["protected_methods"] == ["pin"]
    status, _, body = call(server, "POST", "/identity/sign_out", body={})
    assert status == 200 and body["identity"]["auth_status"] == "guest"


def test_auth_failures_are_audited_but_throttled(server, isolated_audit_log):
    for _ in range(5):
        call(server, "GET", "/memory", token="wrong")
    denied = [e for e in isolated_audit_log.tail() if e["event"] == "bridge.request_denied"]
    assert len(denied) == 1
    assert denied[0]["details"]["path"] == "/memory"
    _, _, body = call(server, "GET", "/audit")
    assert any(e["event"] == "bridge.request_denied" for e in body["entries"])
