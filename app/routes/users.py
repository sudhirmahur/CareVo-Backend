from fastapi import APIRouter, Depends, HTTPException

from app.database import (
    applications_collection,
    interviews_collection,
    jobs_collection,
    notifications_collection,
    premium_requests_collection,
    saved_jobs_collection,
    user_profiles_collection,
    user_skills_collection,
    users_collection,
)
from app.dependencies.auth import get_current_user
from app.routes.auth import public_user
from app.schemas.users import ProfileUpdate, UserUpdate
from app.services import dashboard as dash
from app.services.notify import notify
from app.services.premium import is_premium
from app.services.settings import get_settings
from app.utils import clean_list, now, ser

router = APIRouter()


@router.get("/me")
def get_me(user=Depends(get_current_user)):
    return public_user(user)


@router.put("/me")
def update_me(data: UserUpdate, user=Depends(get_current_user)):
    values = {k: v for k, v in data.model_dump(exclude_unset=True).items()}
    if "name" in values and values["name"] is not None:
        values["name"] = values["name"].strip()
    if not values:
        raise HTTPException(400, "No data provided")
    values["updated_at"] = now()
    users_collection.update_one({"_id": user["_id"]}, {"$set": values})
    return {"message": "User updated successfully"}


def _profile(user) -> dict:
    profile = user_profiles_collection.find_one({"user_id": user["_id"]})
    if not profile:
        # Accounts created by admins/older data may lack a profile row.
        from app.models.users import create_profile_document

        user_profiles_collection.insert_one(create_profile_document(user["_id"]))
        profile = user_profiles_collection.find_one({"user_id": user["_id"]})
    return profile


@router.get("/me/profile")
def get_profile(user=Depends(get_current_user)):
    return ser(_profile(user))


@router.put("/me/profile")
def update_profile(data: ProfileUpdate, user=Depends(get_current_user)):
    _profile(user)
    values = data.model_dump(exclude_unset=True)
    if "skills" in values and values["skills"] is not None:
        values["skills"] = clean_list(values["skills"])
    if not values:
        raise HTTPException(400, "No data provided")
    values["updated_at"] = now()
    user_profiles_collection.update_one({"user_id": user["_id"]}, {"$set": values})
    return {"message": "Profile updated successfully"}


@router.get("/me/dashboard")
def dashboard(user=Depends(get_current_user)):
    uid = user["_id"]
    apps = list(applications_collection.find({"user_id": uid}))
    by_status: dict = {}
    for a in apps:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
    upcoming = interviews_collection.count_documents(
        {"application_id": {"$in": [a["_id"] for a in apps]}, "status": "scheduled", "scheduled_at": {"$gte": now()}}
    ) if apps else 0
    profile = _profile(user)
    comp = dash.completion(user, profile)
    return {
        "applications_total": len(apps),
        "applications_by_status": by_status,
        "saved_jobs": saved_jobs_collection.count_documents({"user_id": uid}),
        "upcoming_interviews": upcoming,
        "unread_notifications": notifications_collection.count_documents({"user_id": uid, "is_read": False}),
        "profile_completeness": comp["percent"],
        "open_jobs": jobs_collection.count_documents({"status": "approved", "is_active": True}),
        "stats": dash.stats(user),
        "completion": comp,
        "recent_activity": dash.recent_activity(user),
        "top_companies": dash.top_companies(),
        "recommended_jobs": dash.recommended_jobs(user, profile),
    }


# ------------------------------------------------------------------ premium
@router.get("/me/premium")
def premium_status(user=Depends(get_current_user)):
    pending = premium_requests_collection.find_one({"user_id": user["_id"], "status": "pending"})
    return {
        "is_premium": is_premium(user),
        "premium_until": ser(user.get("premium_until")),
        "request_pending": bool(pending),
        "gating_enabled": get_settings()["premium_gating"],
    }


@router.post("/me/premium/request", status_code=201)
def premium_request(user=Depends(get_current_user)):
    if user["role"] != "user":
        raise HTTPException(400, "Premium is only for candidate accounts")
    if is_premium(user):
        raise HTTPException(400, "You already have Premium")
    if premium_requests_collection.find_one({"user_id": user["_id"], "status": "pending"}):
        return {"message": "Your request is already with our team. We will contact you soon.", "already_requested": True}
    premium_requests_collection.insert_one({"user_id": user["_id"], "status": "pending", "created_at": now()})
    for admin in users_collection.find({"role": "admin", "is_active": True}, {"_id": 1}):
        notify(admin["_id"], "Premium request", f"{user['name']} ({user['email']}) asked for Premium.", "system", "/admin/users")
    return {"message": "Request sent. Our team will activate Premium for you.", "already_requested": False}
