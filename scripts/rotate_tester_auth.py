"""Rotate the private tester credential (local use only).

Generates a fresh random password (>= 24 chars), stores it ONLY in a
per-user protected directory OUTSIDE the project, and writes the PBKDF2
verifier (never the password) into the project .env with AUTH_ENABLED=true.

Prints no secrets: only paths and format confirmations.
Usage:  python scripts/rotate_tester_auth.py [--user tester]
"""
import hashlib
import os
import secrets
import sys

USER = "tester"
for i, a in enumerate(sys.argv[1:]):
    if a == "--user":
        USER = sys.argv[2 + i]

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(PROJECT, ".env")
SECRETS_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "violetstem-secrets")
PW_PATH = os.path.join(SECRETS_DIR, "tester-password.txt")
ITERS = 600000


def main():
    if not os.path.exists(ENV_PATH):
        raise SystemExit(".env not found; copy .env.example first")
    os.makedirs(SECRETS_DIR, exist_ok=True)
    password = secrets.token_urlsafe(32)  # 43 chars
    assert len(password) >= 24
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERS)
    verifier = f"pbkdf2${ITERS}${salt.hex()}${dk.hex()}"

    sys.path.insert(0, PROJECT)
    from backend.auth import parse_verifier

    kind, _, _, _ = parse_verifier(verifier)
    assert kind == "pbkdf2", "generated verifier rejected by backend parser"

    with open(PW_PATH, "w", encoding="utf-8") as f:
        f.write(password + "\n")
    try:
        os.chmod(PW_PATH, 0o600)
    except Exception:
        pass  # Windows ACLs come from the per-user profile directory

    with open(ENV_PATH, encoding="utf-8") as f:
        lines = f.read().splitlines()
    out, seen = [], set()
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key == "AUTH_ENABLED":
            out.append("AUTH_ENABLED=true")
            seen.add(key)
        elif key == "AUTH_USER":
            out.append(f"AUTH_USER={USER}")
            seen.add(key)
        elif key == "AUTH_PASS_SHA256":
            out.append(f"AUTH_PASS_SHA256={verifier}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in (("AUTH_ENABLED", "true"), ("AUTH_USER", USER),
                     ("AUTH_PASS_SHA256", verifier)):
        if key not in seen:
            out.append(f"{key}={val}")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    print(f"AUTH enabled for user '{USER}'.")
    print(f"Password ({len(password)} chars) stored ONLY at: {PW_PATH}")
    print("Verifier format: pbkdf2-sha256/600k (accepted by backend parser).")
    print(".env updated; restart the VioletStem server to apply.")


if __name__ == "__main__":
    main()
