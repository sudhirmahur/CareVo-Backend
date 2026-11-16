"""Two-factor authentication management + second step of sign-in."""
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.database import challenges_collection, users_collection
from app.dependencies.auth import get_current_user
from app.routes.auth import public_user
from app.services import totp
from app.services.auth import verify_password
from app.services.notify import notify
from app.services.sessions import log_activity
from app.services.twofa import check_code, enabled, finish_login, send_email_code
from app.utils import now

router = APIRouter()


class VerifyIn(BaseModel):
    challenge_token: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=4, max_length=12)


class CodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    challenge_id: str | None = None


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=100)


@router.post("/verify")
def verify_login(data: VerifyIn, request: Request):
    return finish_login(data.challenge_token, data.code, request, public_user)


@router.get("")
def status(user=Depends(get_current_user)):
    t = user.get("twofa") or {}
    return {"enabled": bool(t.get("enabled")), "method": t.get("method"), "pending_totp": bool(t.get("pending_secret"))}


@router.post("/totp/setup")
def totp_setup(user=Depends(get_current_user)):
    if enabled(user):
        raise HTTPException(400, "Two-factor authentication is already on. Turn it off first to change the method.")
    secret = totp.new_secret()
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"twofa": {"enabled": False, "pending_secret": secret}}})
    return {"secret": secret, "otpauth_uri": totp.uri(secret, user["email"])}


@router.post("/totp/enable")
def totp_enable(data: CodeIn, request: Request, user=Depends(get_current_user)):
    secret = (user.get("twofa") or {}).get("pending_secret")
    if not secret:
        raise HTTPException(400, "Start the setup first")
    if not totp.verify(secret, data.code):
        raise HTTPException(400, "That code is not correct. Check the time on your phone and try again.")
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"twofa": {"enabled": True, "method": "totp", "secret": secret, "enabled_at": now()}}})
    log_activity(user["_id"], "2fa_enabled", request, "authenticator app")
    notify(user["_id"], "Two-factor authentication is on", "Sign-ins now need a code from your authenticator app.", "security")
    return {"enabled": True, "method": "totp"}


@router.post("/email/send")
def email_send(user=Depends(get_current_user)):
    if enabled(user):
        raise HTTPException(400, "Two-factor authentication is already on")
    return send_email_code(user, "enable_email")


@router.post("/email/enable")
def email_enable(data: CodeIn, request: Request, user=Depends(get_current_user)):
    try:
        cid = ObjectId(data.challenge_id or "")
    except Exception:
        raise HTTPException(400, "Send a code first")
    ch = challenges_collection.find_one({"_id": cid, "user_id": user["_id"], "purpose": "enable_email"})
    if not ch:
        raise HTTPException(400, "Send a code first")
    if not check_code(ch, user, data.code):
        raise HTTPException(400, "That code is not correct")
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"twofa": {"enabled": True, "method": "email", "enabled_at": now()}}})
    log_activity(user["_id"], "2fa_enabled", request, "e-mail code")
    notify(user["_id"], "Two-factor authentication is on", "Sign-ins now need a code sent to your e-mail.", "security")
    return {"enabled": True, "method": "email"}


@router.post("/disable")
def disable(data: PasswordIn, request: Request, user=Depends(get_current_user)):
    if not verify_password(data.password, user.get("password_hash", "")):
        raise HTTPException(400, "Password is incorrect")
    users_collection.update_one({"_id": user["_id"]}, {"$unset": {"twofa": ""}})
    log_activity(user["_id"], "2fa_disabled", request)
    notify(user["_id"], "Two-factor authentication is off", "Sign-ins no longer need a second step.", "security")
    return {"enabled": False}
