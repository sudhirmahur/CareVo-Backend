"""Encrypts small secrets (e.g. authenticator-app seeds) before they are stored in the database.

Key: DATA_ENCRYPTION_KEY (any long random string). If it is not set the key is derived from JWT_SECRET, which still
protects against a database-only leak, but rotating JWT_SECRET would then make the stored secrets unreadable - so set
DATA_ENCRYPTION_KEY separately in production. Values already stored in plain text keep working (and are encrypted the
next time they are saved).
"""
import base64
import hashlib

from app import config

PREFIX = "enc:v1:"


def _fernet():
    from cryptography.fernet import Fernet

    material = (config.DATA_ENCRYPTION_KEY or config.JWT_SECRET).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"carevo-secretbox|" + material).digest()))


def seal(value: str) -> str:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def reveal(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value or ""  # legacy plain-text value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except Exception:
        return ""  # wrong key / tampered: behaves like "no secret", so verification fails closed
