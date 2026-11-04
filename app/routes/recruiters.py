"""Recruiter public profile (candidates need Premium while gating is on)."""
from fastapi import APIRouter, Depends, HTTPException

from app.database import companies_collection, followers_collection, jobs_collection, user_profiles_collection, users_collection, videos_collection
from app.dependencies.auth import get_current_user
from app.services.jobs import company_snapshot, public_visibility, shape_jobs
from app.services.premium import is_premium
from app.services.settings import get_settings
from app.utils import oid, ser

router = APIRouter()


@router.get("/{recruiter_id}")
def recruiter_profile(recruiter_id: str, user=Depends(get_current_user)):
    rid = oid(recruiter_id, "recruiter ID")
    rec = users_collection.find_one({"_id": rid, "role": "recruiter", "is_active": True})
    if not rec:
        raise HTTPException(404, "Recruiter not found")
    if user["role"] == "user" and get_settings()["premium_gating"] and not is_premium(user):
        raise HTTPException(402, "Premium membership required to view recruiter profiles")
    company = companies_collection.find_one({"recruiter_id": rid, "status": "approved"})
    profile = user_profiles_collection.find_one({"user_id": rid}) or {}
    jobs = list(jobs_collection.find({**public_visibility(), "recruiter_id": rid}).sort("created_at", -1).limit(10))
    return {
        "id": str(rid),
        "name": rec["name"],
        "profile_image": rec.get("profile_image"),
        "headline": profile.get("current_position") or (f"Recruiter at {company['name']}" if company else "Recruiter"),
        "bio": profile.get("bio", ""),
        "location": profile.get("location", ""),
        "company": company_snapshot(company),
        "videos_count": videos_collection.count_documents({"user_id": rid, "is_public": True, "is_removed": {"$ne": True}}),
        "followers_count": followers_collection.count_documents({"following_id": company["_id"]}) if company else 0,
        "jobs": shape_jobs(jobs, user),
    }
