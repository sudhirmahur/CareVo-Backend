"""Password hashing + JWT helpers.

Uses pwdlib (argon2) when installed - exactly what the original project used, so
existing users keep working - and falls back to stdlib PBKDF2 otherwise.
"""
import hashlib
import hmac
import os
from datetime import timedelta

import jwt

from app.config import ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM, JWT_SECRET
from app.utils import now

try:  # pragma: no cover - depends on environment
    from pwdlib import PasswordHash

    _argon = PasswordHash.recommended()
except Exception:  # pragma: no cover
    _argon = None

_PBKDF2_PREFIX = "pbkdf2$"
_PBKDF2_ROUNDS = 240_000


def hash_password(password: str) -> str:
    if _argon is not None:
        return _argon.hash(password)
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"{_PBKDF2_PREFIX}{_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    if hashed.startswith(_PBKDF2_PREFIX):
        try:
            _, rounds, salt_hex, digest_hex = hashed.split("$")
            digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), bytes.fromhex(salt_hex), int(rounds))
            return hmac.compare_digest(digest.hex(), digest_hex)
        except Exception:
            return False
    if _argon is None:
        return False
    try:
        return _argon.verify(plain, hashed)
    except Exception:
        return False


def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now(),
        "exp": now() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
