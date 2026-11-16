"""Login sessions (revocable) and the account activity log."""
import secrets
from datetime import timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request

from app.database import activity_collection, sessions_collection
from app.services.auth import create_access_token
from app.utils import now


def _ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    from app.middleware import client_ip

    return client_ip(request)


def describe_device(ua: str) -> str:
    ua = ua or ""
    low = ua.lower()
    browser = next((n for k, n in (("edg/", "Edge"), ("opr/", "Opera"), ("chrome/", "Chrome"), ("firefox/", "Firefox"), ("safari/", "Safari")) if k in low), "Browser")
    os_ = next((n for k, n in (("windows", "Windows"), ("android", "Android"), ("iphone", "iOS"), ("ipad", "iOS"), ("mac os", "macOS"), ("linux", "Linux")) if k in low), "Unknown OS")
    return f"{browser} on {os_}"


def log_activity(user_id, kind: str, request: Optional[Request] = None, detail: str = "") -> None:
    try:
        activity_collection.insert_one(
            {"user_id": user_id, "kind": kind, "ip": _ip(request), "device": describe_device(request.headers.get("user-agent", "")) if request else "", "detail": detail[:200], "created_at": now()}
        )
    except Exception:  # pragma: no cover - logging must never break a request
        pass


def issue_token(user: dict, request: Optional[Request] = None, kind: str = "login") -> str:
    """Create a revocable session + its JWT, and record the sign-in."""
    sid = secrets.token_hex(12)
    ts = now()
    sessions_collection.insert_one(
        {"user_id": user["_id"], "sid": sid, "ip": _ip(request), "device": describe_device(request.headers.get("user-agent", "")) if request else "Unknown", "created_at": ts, "last_seen": ts, "revoked": False}
    )
    log_activity(user["_id"], kind, request)
    return create_access_token(str(user["_id"]), user["role"], sid)


def touch_session(user_id, sid: str) -> None:
    s = sessions_collection.find_one({"user_id": user_id, "sid": sid})
    if not s or s.get("revoked"):
        raise HTTPException(401, "This session was ended. Please sign in again.")
    seen = s.get("last_seen")
    if seen is not None and seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    if seen is None or now() - seen > timedelta(seconds=60):
        sessions_collection.update_one({"_id": s["_id"]}, {"$set": {"last_seen": now()}})


def revoke_all(user_id, except_sid: Optional[str] = None) -> int:
    q = {"user_id": user_id, "revoked": False}
    if except_sid:
        q["sid"] = {"$ne": except_sid}
    return sessions_collection.update_many(q, {"$set": {"revoked": True, "revoked_at": now()}}).modified_count
