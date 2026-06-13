"""FamBank – Passwort-Hashing & Session-Helfer.

Bewusst ohne externe Krypto-Abhängigkeit: PBKDF2-HMAC-SHA256 aus hashlib.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Optional

import sqlite3

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> Optional[sqlite3.Row]:
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
    ).fetchone()
    if row and verify_password(password, row["password_hash"]):
        return row
    return None


def session_secret() -> str:
    """Stabiler Session-Schlüssel; bei Bedarf einmalig generiert und in data/ abgelegt."""
    from db import DATA_DIR

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, ".session_secret")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    secret = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(secret)
    os.chmod(path, 0o600)
    return secret
