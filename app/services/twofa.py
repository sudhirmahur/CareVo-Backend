"""Two-factor sign-in: authenticator app (TOTP) or e-mailed one-time code."""
import hmac
import secrets
from datetime import timedelta, timezone
from typing import Callable, Optional

import jwt
from fastapi import HTTPException, Request

from app import config
from app.database import challenges_collection, users_collection
from app.services import totp
from app.services.auth import hash_token
from app.services.email import send_template
from app.services.sessions import issue_token, log_activity
from app.utils import now

CHALLENGE_MINUTES = 10
MAX_ATTEMPTS = 5


def enabled(user: dict) -> bool:
    return bool((user.get("twofa") or {}).get("enabled"))


def _new_email_code(user: dict, purpose: str) -> tuple[object, str]:
    code = f"{secrets.randbelow(1_000_000):06d}"
    cid = challenges_collection.insert_one(
        {"user_id": user["_id"], "method": "email", "purpose": purpose, "code_hash": hash_token(code), "attempts": 0, "used": False, "created_at": now(), "expires_at": now() + timedelta(minutes=CHALLENGE_MINUTES)}
    ).inserted_id
    send_template("otp", user["email"], {"name": user["name"], "code": code}, user["_id"], force=True)
    return cid, code


def send_email_code(user: dict, purpose: str) -> dict:
    cid, code = _new_email_code(user, purpose)
    out = {"challenge_id": str(cid)}
    if config.EXPOSE_OTP:
        out["dev_code"] = code
    return out


def login_result(user: dict, request: Request, public_user: Callable[[dict], dict]) -> dict:
    if not enabled(user):
        return {"message": "Login successful", "access_token": issue_token(user, request), "token_type": "bearer", "user": public_user(user)}
    method = user["twofa"]["method"]
    out = {"requires_2fa": True, "method": method, "message": "Enter your verification code"}
    if method == "email":
        cid, code = _new_email_code(user, "login")
        if config.EXPOSE_OTP:
            out["dev_code"] = code
    else:
        cid = challenges_collection.insert_one(
            {"user_id": user["_id"], "method": "totp", "purpose": "login", "attempts": 0, "used": False, "created_at": now(), "expires_at": now() + timedelta(minutes=CHALLENGE_MINUTES)}
        ).inserted_id
    out["challenge_token"] = jwt.encode({"sub": str(user["_id"]), "cid": str(cid), "typ": "2fa", "exp": now() + timedelta(minutes=CHALLENGE_MINUTES)}, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)
    return out


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def check_code(ch: dict, user: dict, code: str) -> bool:
    """Validate a code against a stored challenge, counting attempts. Raises on exhausted/expired challenges."""
    if ch.get("used") or _aware(ch["expires_at"]) < now():
        raise HTTPException(400, "This code has expired. Please sign in again.")
    if ch["attempts"] >= MAX_ATTEMPTS:
        raise HTTPException(429, "Too many wrong codes. Please sign in again.")
    challenges_collection.update_one({"_id": ch["_id"]}, {"$inc": {"attempts": 1}})
    if ch["method"] == "email":
        ok = hmac.compare_digest(ch["code_hash"], hash_token((code or "").strip()))
    else:
        ok = totp.verify((user.get("twofa") or {}).get("secret", ""), code)
    if ok:
        challenges_collection.update_one({"_id": ch["_id"]}, {"$set": {"used": True}})
    return ok


def finish_login(challenge_token: str, code: str, request: Request, public_user: Callable[[dict], dict]) -> dict:
    try:
        payload = jwt.decode(challenge_token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(400, "This sign-in request expired. Please start again.")
    if payload.get("typ") != "2fa":
        raise HTTPException(400, "Invalid sign-in request")
    from bson import ObjectId

    ch = challenges_collection.find_one({"_id": ObjectId(payload["cid"]), "user_id": ObjectId(payload["sub"]), "purpose": "login"})
    user = users_collection.find_one({"_id": ObjectId(payload["sub"])})
    if not ch or not user or not user.get("is_active", False):
        raise HTTPException(400, "Invalid sign-in request")
    if not check_code(ch, user, code):
        log_activity(user["_id"], "2fa_failed", request)
        raise HTTPException(401, "That code is not correct")
    return {"message": "Login successful", "access_token": issue_token(user, request), "token_type": "bearer", "user": public_user(user)}
