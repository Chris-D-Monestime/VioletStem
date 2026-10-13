"""Auth tests: private tester access via env-gated HTTP Basic.

The local default (AUTH_ENABLED=false) must stay fully open; with auth on,
EVERY route (frontend + API + downloads) requires credentials, and unguessable
job IDs still 404 for authenticated strangers. The middleware reads settings
per request, so tests toggle it with monkeypatch (no app rebuilds).
"""
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from backend import app as appmod
from backend import auth as authmod
from backend.config import settings

USER = "tester-fase4"
PASS = "c4mb14m3-esta-clave-es-solo-de-test"
PASS_HEX = hashlib.sha256(PASS.encode()).hexdigest()

SAMPLE = os.path.abspath("tests/assets/sample.mp3")


def test_check_unit():
    assert authmod._check(USER, PASS, USER, PASS_HEX) is True
    assert authmod._check(USER, "wrong", USER, PASS_HEX) is False
    assert authmod._check("other", PASS, USER, PASS_HEX) is False
    assert authmod._check(USER, PASS, "", PASS_HEX) is False
    assert authmod._check(USER, PASS, USER, "not-hex") is False
    assert authmod._check(USER, PASS, USER, "ab" * 32) is False  # valid hex, wrong hash


def _pbkdf2_verifier(password: str, iters: int = 120000) -> str:
    import hashlib as _hl
    import secrets as _secrets
    salt = _secrets.token_bytes(16)
    dk = _hl.pbkdf2_hmac("sha256", password.encode(), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${dk.hex()}"


def test_check_pbkdf2_and_malformed():
    good = _pbkdf2_verifier(PASS)
    assert authmod._check(USER, PASS, USER, good) is True
    assert authmod._check(USER, "wrong", USER, good) is False
    assert authmod._check("other", PASS, USER, good) is False
    # malformed verifiers never authenticate
    assert authmod._check(USER, PASS, USER, "pbkdf2$10$abcd$ef") is False
    assert authmod._check(USER, PASS, USER, "pbkdf2$600000$zz$ee") is False
    assert authmod._check(USER, PASS, USER, "pbkdf2$600000$" + "ab" * 4 + "$" + "cd" * 16) is False
    kind, _, _, _ = authmod.parse_verifier(good)
    assert kind == "pbkdf2"
    kind, _, _, _ = authmod.parse_verifier(PASS_HEX)
    assert kind == "legacy"


@pytest.fixture()
def throttle_env(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_user", USER)
    monkeypatch.setattr(settings, "auth_pass_sha256", _pbkdf2_verifier(PASS))
    monkeypatch.setattr(settings, "auth_max_attempts", 3)
    monkeypatch.setattr(settings, "auth_block_window_seconds", 300)
    authmod._fails.clear()
    yield
    authmod._fails.clear()


def test_throttle_wrong_attempts_then_429(throttle_env):
    c = TestClient(appmod.app)
    for _ in range(3):
        assert c.get("/api/health", auth=(USER, "nope")).status_code == 401
    assert c.get("/api/health", auth=(USER, "nope")).status_code == 429
    # correct credentials are never locked out and reset the counter
    assert c.get("/api/health", auth=(USER, PASS)).status_code == 200
    assert c.get("/api/health", auth=(USER, "nope")).status_code == 401


def test_missing_credentials_are_not_counted(throttle_env):
    c = TestClient(appmod.app)
    for _ in range(10):
        assert c.get("/api/health").status_code == 401
    for _ in range(3):
        assert c.get("/api/health", auth=(USER, "nope")).status_code == 401
    assert c.get("/api/health", auth=(USER, "nope")).status_code == 429


def test_401_body_leaks_nothing(throttle_env):
    c = TestClient(appmod.app)
    r = c.get("/api/health", auth=(USER, "wrong"))
    body = r.text.lower()
    for secret in ("sha256", "pbkdf2", USER.lower(), "c:\\", "/srv", ".env"):
        assert secret not in body, secret


@pytest.fixture()
def authed_client(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_user", USER)
    monkeypatch.setattr(settings, "auth_pass_sha256", PASS_HEX)
    yield TestClient(appmod.app)


@pytest.fixture()
def open_client():
    assert settings.auth_enabled is False
    return TestClient(appmod.app)


def test_open_by_default(open_client):
    assert open_client.get("/").status_code == 200
    assert open_client.get("/api/health").status_code == 200


def test_auth_required_everywhere(authed_client):
    c = authed_client
    r = c.get("/")
    assert r.status_code == 401
    assert "basic" in r.headers.get("www-authenticate", "").lower()
    assert c.get("/api/health").status_code == 401
    assert c.get("/api/engine").status_code == 401
    with open(SAMPLE, "rb") as f:
        data = f.read()
    r = c.post("/api/separations", files={"file": ("s.mp3", data, "audio/mpeg")})
    assert r.status_code == 401


def test_wrong_credentials_rejected(authed_client):
    c = authed_client
    assert c.get("/api/health", auth=("tester-fase4", "nope")).status_code == 401
    assert c.get("/api/health", auth=("intruso", PASS)).status_code == 401


def test_correct_credentials_full_flow(authed_client):
    c = authed_client
    cred = (USER, PASS)
    assert c.get("/api/health", auth=cred).status_code == 200
    assert c.get("/", auth=cred).status_code == 200
    with open(SAMPLE, "rb") as f:
        data = f.read()
    r = c.post("/api/separations", files={"file": ("s.mp3", data, "audio/mpeg")},
               auth=cred)
    assert r.status_code == 201, r.text
    jid = r.json()["job_id"]
    assert len(jid) >= 16
    import time
    for _ in range(120):
        j = c.get(f"/api/separations/{jid}", auth=cred).json()
        if j["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.5)
    assert j["status"] == "completed", j
    assert "demucs" in (j["engine"] or "")
    # downloads require auth too
    assert c.get(f"/api/separations/{jid}/vocals").status_code == 401
    rv = c.get(f"/api/separations/{jid}/vocals", auth=cred)
    assert rv.status_code == 200 and len(rv.content) > 1000
    # ...but a stranger's guessed ID still 404s even authenticated
    assert c.get("/api/separations/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", auth=cred).status_code == 404
    c.delete(f"/api/separations/{jid}", auth=cred)
