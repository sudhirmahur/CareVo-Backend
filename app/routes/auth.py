import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone

from app.config import (
    EXPOSE_RESET_TOKEN,
    GOOGLE_CLIENT_ID,
    GOOGLE_TOKENINFO_URL,
    LOCKOUT_MINUTES,
    MAX_FAILED_LOGINS,
    RESET_TOKEN_MINUTES,
)
from app.database import (
    password_resets_collection,
    user_profiles_collection,
    users_collection,
)
from app.dependencies.auth import get_current_user
from app.models.users import create_profile_document, create_user_document
from app.schemas.users import (
    ChangePassword,
    ForgotPassword,
    GoogleLogin,
    ResetPassword,
    UserLogin,
    UserRegister,
)
from app.services.auth import create_access_token, hash_password, hash_token, verify_password
from app.services.notify import notify
from app.services.premium import is_premium
from app.services.settings import get_settings
from app.utils import now, ser

router = APIRouter()


def public_user(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "phone": user.get("phone"),
        "profile_image": user.get("profile_image"),
        "is_active": user.get("is_active", True),
        "is_premium": is_premium(user) if user["role"] == "user" else False,
        "premium_until": ser(user.get("premium_until")),
        "auth_provider": user.get("auth_provider", "email"),
        "created_at": ser(user.get("created_at")),
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(data: UserRegister):
    if not get_settings()["allow_registration"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "New registrations are currently disabled")
    if users_collection.find_one({"email": data.email}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    doc = create_user_document(
        name=data.name.strip(),
        email=data.email,
        password_hash=hash_password(data.password),
        role=data.role,
        phone=(data.phone or None),
    )
    user_id = users_collection.insert_one(doc).inserted_id
    user_profiles_collection.insert_one(create_profile_document(user_id))
    doc["_id"] = user_id

    # Sign the user in right away so the client can go straight to the dashboard.
    token = create_access_token(str(user_id), doc["role"])
    return {
        "message": "Registration successful",
        "user_id": str(user_id),
        "access_token": token,
        "token_type": "bearer",
        "user": public_user(doc),
    }


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


@router.post("/login")
def login(data: UserLogin):
    user = users_collection.find_one({"email": data.email})
    if user:
        locked = _aware(user.get("locked_until"))
        if locked and locked > now():
            minutes = max(1, int((locked - now()).total_seconds() // 60) + 1)
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Too many failed attempts. Try again in {minutes} minute(s).")
    if not user or not verify_password(data.password, user.get("password_hash", "")):
        if user:
            fails = int(user.get("failed_logins", 0)) + 1
            update = {"failed_logins": fails}
            if fails >= MAX_FAILED_LOGINS:
                update = {"failed_logins": 0, "locked_until": now() + timedelta(minutes=LOCKOUT_MINUTES)}
            users_collection.update_one({"_id": user["_id"]}, {"$set": update})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.get("is_active", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is blocked or inactive")
    if get_settings()["maintenance_mode"] and user["role"] != "admin":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Platform is under maintenance. Try again later.")
    if user.get("failed_logins") or user.get("locked_until"):
        users_collection.update_one({"_id": user["_id"]}, {"$set": {"failed_logins": 0, "locked_until": None}})
    token = create_access_token(str(user["_id"]), user["role"])
    return {
        "message": "Login successful",
        "access_token": token,
        "token_type": "bearer",
        "user": public_user(user),
    }


def _google_profile(access_token: str) -> dict:
    """Ask Google who owns this token and make sure it was issued to OUR client id."""
    url = f"{GOOGLE_TOKENINFO_URL}?access_token={urllib.parse.quote(access_token, safe='')}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - fixed https endpoint
            info = json.loads(resp.read().decode())
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed. Please try again.")
    if info.get("aud") != GOOGLE_CLIENT_ID and info.get("azp") != GOOGLE_CLIENT_ID:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed. Please try again.")
    if str(info.get("email_verified")).lower() != "true" or not info.get("email"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your Google email is not verified.")
    return info


@router.post("/google")
def google_login(data: GoogleLogin):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Google sign-in is not configured on this server")
    info = _google_profile(data.access_token)
    email = info["email"].strip().lower()
    user = users_collection.find_one({"email": email})
    if user and not user.get("is_active", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is blocked or inactive")
    if get_settings()["maintenance_mode"] and (not user or user["role"] != "admin"):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Platform is under maintenance. Try again later.")
    if not user:
        if not get_settings()["allow_registration"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "New registrations are currently disabled")
        name = (info.get("name") or email.split("@")[0])[:100]
        doc = create_user_document(name, email, hash_password(secrets.token_urlsafe(32)), role=data.role)
        doc["auth_provider"] = "google"
        doc["provider_id"] = info.get("sub")
        doc["_id"] = users_collection.insert_one(dict(doc)).inserted_id
        user_profiles_collection.insert_one(create_profile_document(doc["_id"]))
        user = doc
    elif not user.get("provider_id") and info.get("sub"):
        users_collection.update_one({"_id": user["_id"]}, {"$set": {"provider_id": info["sub"]}})
    token = create_access_token(str(user["_id"]), user["role"])
    return {"message": "Login successful", "access_token": token, "token_type": "bearer", "user": public_user(user)}


@router.get("/me")
def me(user=Depends(get_current_user)):
    return public_user(user)


@router.post("/forgot-password")
def forgot_password(data: ForgotPassword):
    """Always answers the same way so e-mails cannot be enumerated."""
    response = {"message": "If that email is registered, a reset link has been sent"}
    user = users_collection.find_one({"email": data.email})
    if not user:
        return response
    token = secrets.token_urlsafe(32)
    password_resets_collection.insert_one(
        {
            "user_id": user["_id"],
            "token_hash": hash_token(token),
            "expires_at": now() + timedelta(minutes=RESET_TOKEN_MINUTES),
            "used": False,
            "created_at": now(),
        }
    )
    if EXPOSE_RESET_TOKEN:  # development convenience only
        response["reset_token"] = token
    return response


@router.post("/reset-password")
def reset_password(data: ResetPassword):
    record = password_resets_collection.find_one({"token_hash": hash_token(data.token), "used": False})
    expires = record["expires_at"] if record else None
    if expires is not None and expires.tzinfo is None:
        from datetime import timezone

        expires = expires.replace(tzinfo=timezone.utc)
    if not record or expires < now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reset link is invalid or has expired")
    users_collection.update_one(
        {"_id": record["user_id"]},
        {"$set": {"password_hash": hash_password(data.password), "updated_at": now(), "tokens_valid_after": now(), "failed_logins": 0, "locked_until": None}},
    )
    password_resets_collection.update_one({"_id": record["_id"]}, {"$set": {"used": True}})
    return {"message": "Password has been reset. You can sign in now."}


@router.post("/change-password")
def change_password(data: ChangePassword, user=Depends(get_current_user)):
    if not verify_password(data.current_password, user.get("password_hash", "")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": hash_password(data.new_password), "updated_at": now(), "tokens_valid_after": now()}},
    )
    notify(user["_id"], "Password changed", "Your password was changed successfully.", "system")
    # every older session (stolen tokens included) is now invalid; give this browser a fresh one
    return {"message": "Password updated", "access_token": create_access_token(str(user["_id"]), user["role"]), "token_type": "bearer"}
