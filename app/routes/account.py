"""Account centre shared by candidates, recruiters and admins: sessions, activity, notification preferences,
privacy + data controls, blocking."""
import json
from typing import Literal, Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.config import JWT_ALGORITHM, JWT_SECRET
from app.database import (
    activity_collection,
    applications_collection,
    blocks_collection,
    deletion_requests_collection,
    messages_collection,
    notifications_collection,
    saved_jobs_collection,
    sessions_collection,
    user_profiles_collection,
    users_collection,
)
from app.dependencies.auth import get_current_user, security
from app.services.auth import verify_password
from app.services.notify import notify
from app.services.sessions import log_activity, revoke_all
from app.utils import get_or_404, now, oid, ser

router = APIRouter()

CATEGORIES = ["jobs", "applications", "interviews", "messages", "system", "promotions"]


def _current_sid(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]).get("sid")
    except Exception:
        return None


# ------------------------------------------------------------ sessions + activity
@router.get("/sessions")
def sessions(user=Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(security)):
    sid = _current_sid(credentials)
    rows = sessions_collection.find({"user_id": user["_id"], "revoked": False}).sort("last_seen", -1).limit(30)
    out = []
    for s in rows:
        item = ser({k: v for k, v in s.items() if k != "sid"})
        item["current"] = s.get("sid") == sid
        item["id"] = str(s["_id"])
        out.append(item)
    return {"items": out}


@router.delete("/sessions/{session_id}")
def end_session(session_id: str, user=Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(security)):
    s = get_or_404(sessions_collection, oid(session_id, "session ID"), "Session", {"user_id": user["_id"]})
    if s.get("sid") == _current_sid(credentials):
        raise HTTPException(400, "This is the session you are using. Use Sign out instead.")
    sessions_collection.update_one({"_id": s["_id"]}, {"$set": {"revoked": True, "revoked_at": now()}})
    return {"message": "Session ended"}


@router.delete("/sessions")
def end_other_sessions(request: Request, user=Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(security)):
    n = revoke_all(user["_id"], _current_sid(credentials))
    log_activity(user["_id"], "sessions_ended", request, f"{n} other session(s)")
    return {"message": f"Signed out of {n} other device(s)", "ended": n}


@router.get("/activity")
def activity(limit: int = Query(30, ge=1, le=100), user=Depends(get_current_user)):
    rows = activity_collection.find({"user_id": user["_id"]}).sort("created_at", -1).limit(limit)
    return {"items": [ser(r) for r in rows]}


# ------------------------------------------------------------ notification preferences
class ChannelPrefs(BaseModel):
    in_app: bool = True
    email: bool = True


class NotifPrefsIn(BaseModel):
    email_enabled: bool = True
    in_app_enabled: bool = True
    categories: dict[Literal["jobs", "applications", "interviews", "messages", "system", "promotions"], ChannelPrefs] = Field(default_factory=dict)
    interview_reminders: bool = True
    job_reminders: bool = True


def _prefs(user: dict) -> dict:
    p = user.get("notification_prefs") or {}
    cats = {c: {"in_app": True, "email": True, **(p.get("categories", {}).get(c) or {})} for c in CATEGORIES}
    return {"email_enabled": p.get("email_enabled", True), "in_app_enabled": p.get("in_app_enabled", True), "categories": cats, "interview_reminders": p.get("interview_reminders", True), "job_reminders": p.get("job_reminders", True)}


@router.get("/notification-preferences")
def get_prefs(user=Depends(get_current_user)):
    return _prefs(user)


@router.put("/notification-preferences")
def put_prefs(data: NotifPrefsIn, user=Depends(get_current_user)):
    doc = data.model_dump()
    if not data.in_app_enabled:
        for c in doc["categories"].values():
            c["in_app"] = False
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"notification_prefs": doc, "updated_at": now()}})
    return _prefs({"notification_prefs": doc})


# ------------------------------------------------------------ privacy
class PrivacyIn(BaseModel):
    profile_visibility: Literal["public", "recruiters", "private"] = "recruiters"
    show_email: bool = False
    show_phone: bool = False
    allow_messages: Literal["everyone", "applied", "none"] = "everyone"
    marketing_emails: bool = True


def _privacy(user: dict) -> dict:
    return {"profile_visibility": "recruiters", "show_email": False, "show_phone": False, "allow_messages": "everyone", "marketing_emails": True, **(user.get("privacy") or {})}


@router.get("/privacy")
def get_privacy(user=Depends(get_current_user)):
    return _privacy(user)


@router.put("/privacy")
def put_privacy(data: PrivacyIn, request: Request, user=Depends(get_current_user)):
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"privacy": data.model_dump(), "updated_at": now()}})
    log_activity(user["_id"], "privacy_changed", request)
    return _privacy({"privacy": data.model_dump()})


@router.get("/export")
def export_my_data(request: Request, user=Depends(get_current_user)):
    uid = user["_id"]
    data = {
        "exported_at": now(),
        "account": {k: user.get(k) for k in ("name", "email", "phone", "role", "created_at", "privacy", "notification_prefs")},
        "profile": user_profiles_collection.find_one({"user_id": uid}),
        "applications": list(applications_collection.find({"user_id": uid})),
        "saved_jobs": list(saved_jobs_collection.find({"user_id": uid})),
        "notifications": list(notifications_collection.find({"user_id": uid}).limit(500)),
        "messages_sent": list(messages_collection.find({"sender_id": uid}).limit(1000)),
        "activity": list(activity_collection.find({"user_id": uid}).sort("created_at", -1).limit(500)),
    }
    log_activity(uid, "data_exported", request)
    return Response(json.dumps(ser(data), indent=2), media_type="application/json", headers={"Content-Disposition": "attachment; filename=carevo-my-data.json"})


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=100)
    reason: Optional[str] = Field(default=None, max_length=300)


@router.post("/deactivate")
def deactivate(data: PasswordIn, request: Request, user=Depends(get_current_user)):
    if user["role"] == "admin":
        raise HTTPException(400, "Admin accounts cannot be deactivated here")
    if not verify_password(data.password, user.get("password_hash", "")):
        raise HTTPException(400, "Password is incorrect")
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"is_active": False, "self_deactivated": True, "deactivated_at": now()}})
    revoke_all(user["_id"])
    log_activity(user["_id"], "deactivated", request)
    return {"message": "Your account is deactivated. Sign in again any time to reactivate it."}


@router.post("/deletion-request", status_code=201)
def request_deletion(data: PasswordIn, user=Depends(get_current_user)):
    if user["role"] == "admin":
        raise HTTPException(400, "Admin accounts cannot be deleted here")
    if not verify_password(data.password, user.get("password_hash", "")):
        raise HTTPException(400, "Password is incorrect")
    if deletion_requests_collection.find_one({"user_id": user["_id"], "status": "pending"}):
        return {"message": "Your deletion request is already with our team.", "already_requested": True}
    deletion_requests_collection.insert_one({"user_id": user["_id"], "reason": (data.reason or "").strip(), "status": "pending", "created_at": now()})
    for a in users_collection.find({"role": "admin", "is_active": True}, {"_id": 1}):
        notify(a["_id"], "Account deletion request", f"{user['name']} ({user['email']}) asked to delete their account.", "system", "/admin/users")
    return {"message": "Request received. Our team will delete your account and data.", "already_requested": False}


@router.get("/deletion-request")
def my_deletion_request(user=Depends(get_current_user)):
    r = deletion_requests_collection.find_one({"user_id": user["_id"], "status": "pending"})
    return {"pending": bool(r), "created_at": ser(r["created_at"]) if r else None}


@router.delete("/deletion-request")
def cancel_deletion(user=Depends(get_current_user)):
    n = deletion_requests_collection.update_many({"user_id": user["_id"], "status": "pending"}, {"$set": {"status": "cancelled", "resolved_at": now()}}).modified_count
    return {"message": "Request cancelled" if n else "Nothing to cancel"}


# ------------------------------------------------------------ blocking
class BlockIn(BaseModel):
    user_id: str


def is_blocked_between(a, b) -> bool:
    return bool(blocks_collection.find_one({"$or": [{"blocker_id": a, "blocked_id": b}, {"blocker_id": b, "blocked_id": a}]}))


@router.get("/blocks")
def my_blocks(user=Depends(get_current_user)):
    rows = list(blocks_collection.find({"blocker_id": user["_id"]}).sort("created_at", -1))
    people = {u["_id"]: u for u in users_collection.find({"_id": {"$in": [r["blocked_id"] for r in rows]}}, {"name": 1, "role": 1, "profile_image": 1})}
    return {"items": [{"user_id": str(r["blocked_id"]), "name": people[r["blocked_id"]]["name"], "role": people[r["blocked_id"]]["role"], "blocked_at": ser(r["created_at"])} for r in rows if r["blocked_id"] in people]}


@router.post("/blocks", status_code=201)
def block(data: BlockIn, user=Depends(get_current_user)):
    target = get_or_404(users_collection, oid(data.user_id, "user ID"), "User")
    if target["_id"] == user["_id"]:
        raise HTTPException(400, "You cannot block yourself")
    if target["role"] == "admin":
        raise HTTPException(400, "Administrators cannot be blocked")
    if not blocks_collection.find_one({"blocker_id": user["_id"], "blocked_id": target["_id"]}):
        blocks_collection.insert_one({"blocker_id": user["_id"], "blocked_id": target["_id"], "created_at": now()})
    return {"message": f"{target['name']} is blocked", "blocked": True}


@router.delete("/blocks/{user_id}")
def unblock(user_id: str, user=Depends(get_current_user)):
    blocks_collection.delete_one({"blocker_id": user["_id"], "blocked_id": oid(user_id, "user ID")})
    return {"message": "Unblocked", "blocked": False}
