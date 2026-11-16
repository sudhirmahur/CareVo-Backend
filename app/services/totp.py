"""RFC 6238 time-based one-time passwords (Google Authenticator, Authy, 1Password ...). Stdlib only."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def verify(secret: str, code: str, window: int = 1, at: float | None = None) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return False
    step = int((at if at is not None else time.time()) // 30)
    return any(hmac.compare_digest(_code(secret, step + d), code) for d in range(-window, window + 1))


def current(secret: str) -> str:
    return _code(secret, int(time.time() // 30))


def uri(secret: str, email: str, issuer: str = "CareVo") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"
