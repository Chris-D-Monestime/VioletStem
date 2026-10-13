"""Optional HTTP Basic authentication for private tester access.

Disabled by default (AUTH_ENABLED=false) — local use is unaffected.
When enabled, EVERY route (API + static frontend) requires valid credentials.

Credential storage (env AUTH_PASS_SHA256 — name kept for compatibility):
- Preferred: PBKDF2-HMAC-SHA256 string
  `pbkdf2$<iterations>$<salt-hex-16B>$<dk-hex-32B>` (e.g. 600000 iterations).
- Legacy: plain 64-hex-char SHA-256 digest (still accepted, discouraged for
  new credentials — fast hashes are cheap to brute-force if ever leaked).

Comparisons use hmac.compare_digest (constant time). Failed attempts with
wrong credentials are throttled per client IP (AUTH_MAX_ATTEMPTS / 5 min
window default): beyond the cap, wrong attempts get 429. Correct credentials
always reset the counter and are never locked out. Attempts WITHOUT any
credentials are not counted (every browser first-visit would count otherwise).
"""
from __future__ import annotations
import base64
import binascii
import hashlib
import hmac
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_PBKDF2_MIN_ITER = 100_000
_PBKDF2_MAX_ITER = 2_000_000

# ip -> [unix timestamps of recent WRONG-credential failures]
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def parse_verifier(conf_hex: str):
    """Return ('pbkdf2', iterations, salt, dk) or ('legacy', None, None, digest)
    or (None, ...) if malformed."""
    s = (conf_hex or "").strip().lower()
    if s.startswith("pbkdf2$"):
        try:
            _, it, salt_h, dk_h = s.split("$")
            it = int(it)
            salt = binascii.unhexlify(salt_h)
            dk = binascii.unhexlify(dk_h)
        except (ValueError, binascii.Error):
            return None, None, None, None
        if not (_PBKDF2_MIN_ITER <= it <= _PBKDF2_MAX_ITER):
            return None, None, None, None
        if len(salt) != 16 or len(dk) != 32:
            return None, None, None, None
        return "pbkdf2", it, salt, dk
    if len(s) == 64:
        try:
            return "legacy", None, None, binascii.unhexlify(s)
        except (binascii.Error, ValueError):
            return None, None, None, None
    return None, None, None, None


def _check(user: str, password: str, conf_user: str, conf_hex: str) -> bool:
    if not conf_user:
        return False
    kind, it, salt, want = parse_verifier(conf_hex)
    if kind is None:
        return False
    if kind == "pbkdf2":
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, it)
    else:
        got = hashlib.sha256(password.encode("utf-8")).digest()
    return hmac.compare_digest(user, conf_user) and hmac.compare_digest(got, want)


def _failure(ip: str, max_attempts: int, window: int) -> bool:
    """Record a wrong-credential attempt. Return True if the IP is now over
    the cap (caller must then answer 429)."""
    now = time.time()
    with _fails_lock:
        hist = [t for t in _fails.get(ip, []) if now - t < window]
        hist.append(now)
        _fails[ip] = hist
        return len(hist) > max_attempts


def _reset(ip: str) -> None:
    with _fails_lock:
        _fails.pop(ip, None)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Always installed; no-op unless settings.auth_enabled. Reads settings
    per request so tests can toggle it without app rebuilds."""

    async def dispatch(self, request, call_next):
        from backend.config import settings

        if not settings.auth_enabled:
            return await call_next(request)
        conf_user, conf_hex = settings.auth_user, settings.auth_pass_sha256
        ip = request.client.host if request.client else "unknown"
        auth = request.headers.get("authorization", "")
        has_creds = auth.lower().startswith("basic ")
        ok = False
        if has_creds:
            try:
                decoded = base64.b64decode(auth[6:].strip()).decode("utf-8")
                user, _, password = decoded.partition(":")
                ok = _check(user, password, conf_user, conf_hex)
            except Exception:
                ok = False
        if ok:
            _reset(ip)  # correct credentials are never locked out
            return await call_next(request)
        if has_creds and _failure(
            ip,
            settings.auth_max_attempts,
            settings.auth_block_window_seconds,
        ):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many failed login attempts. Try again later."},
            )
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required."},
            headers={"WWW-Authenticate": 'Basic realm="VioletStem"'},
        )
