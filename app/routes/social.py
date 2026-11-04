"""Companies (public), follow, reports, banners and landing-page stats."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import (
    banners_collection,
    companies_collection,
    followers_collection,
    jobs_collection,
    reports_collection,
    users_collection,
    videos_collection,
    video_comments_collection,
)
from app.dependencies.auth import get_current_user, get_optional_user
from app.schemas.social import FollowIn, ReportIn
from app.services.jobs import public_visibility, shape_jobs
from app.services.notify import notify
from app.utils import get_or_404, now, oid, paginate, rx, ser

router = APIRouter()


# --------------------------------------------------------- companies


def _company_view(c: dict, user: Optional[dict]) -> dict:
    out = ser(c)
    out["followers_count"] = followers_collection.count_documents({"following_id": c["_id"], "type": "company"})
    out["open_jobs"] = jobs_collection.count_documents({**public_visibility(), "company_id": c["_id"]})
    out["is_following"] = bool(
        user and followers_collection.find_one({"follower_id": user["_id"], "following_id": c["_id"]})
    )
    return out


@router.get("/companies")
def list_companies(
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=12, ge=1, le=50),
    user=Depends(get_optional_user),
):
    query: dict = {"status": "approved"}
    if search:
        query["name"] = rx(search)
    result = paginate(companies_collection, query, page, limit, [("name", 1)], mapper=lambda d: d)
    result["items"] = [_company_view(c, user) for c in result["items"]]
    return result


@router.get("/companies/{company_id}")
def get_company(company_id: str, user=Depends(get_optional_user)):
    c = get_or_404(companies_collection, oid(company_id, "company ID"), "Company")
    if c.get("status") != "approved" and not (user and (user["role"] == "admin" or c["recruiter_id"] == user["_id"])):
        raise HTTPException(404, "Company not found")
    out = _company_view(c, user)
    jobs = list(jobs_collection.find({**public_visibility(), "company_id": c["_id"]}).sort("created_at", -1).limit(20))
    out["jobs"] = shape_jobs(jobs, user)
    out["recruiter_id"] = str(c["recruiter_id"])
    return out


# ------------------------------------------------------------ follow


@router.post("/follow", status_code=201)
def follow(data: FollowIn, user=Depends(get_current_user)):
    target = oid(data.following_id, "ID")
    if data.type == "company":
        company = get_or_404(companies_collection, target, "Company")
        owner = company["recruiter_id"]
    else:
        if target == user["_id"]:
            raise HTTPException(400, "You cannot follow yourself")
        get_or_404(users_collection, target, "User")
        owner = target
    if owner == user["_id"]:
        raise HTTPException(400, "You cannot follow yourself")
    res = followers_collection.update_one(
        {"follower_id": user["_id"], "following_id": target},
        {"$setOnInsert": {"follower_id": user["_id"], "following_id": target, "type": data.type, "created_at": now()}},
        upsert=True,
    )
    if res.upserted_id:
        notify(owner, "New follower", f"{user['name']} started following you.", "system")
    return {"message": "Following", "is_following": True}


@router.delete("/follow/{following_id}")
def unfollow(following_id: str, user=Depends(get_current_user)):
    followers_collection.delete_one({"follower_id": user["_id"], "following_id": oid(following_id, "ID")})
    return {"message": "Unfollowed", "is_following": False}


@router.get("/follow/following")
def my_following(user=Depends(get_current_user)):
    rows = list(followers_collection.find({"follower_id": user["_id"]}).sort("created_at", -1))
    company_ids = [r["following_id"] for r in rows if r["type"] == "company"]
    user_ids = [r["following_id"] for r in rows if r["type"] == "user"]
    companies = {c["_id"]: c for c in companies_collection.find({"_id": {"$in": company_ids}})}
    people = {u["_id"]: u for u in users_collection.find({"_id": {"$in": user_ids}})}
    items = []
    for r in rows:
        src = companies.get(r["following_id"]) if r["type"] == "company" else people.get(r["following_id"])
        if src:
            items.append({"id": str(r["following_id"]), "type": r["type"], "name": src["name"], "logo_url": src.get("logo_url") or src.get("profile_image")})
    return {"items": items, "total": len(items)}


# ----------------------------------------------------------- reports


@router.post("/reports", status_code=201)
def create_report(data: ReportIn, user=Depends(get_current_user)):
    cid = oid(data.content_id, "content ID")
    target = {"job": jobs_collection, "user": users_collection, "video": videos_collection, "comment": video_comments_collection}[data.content_type]
    get_or_404(target, cid, "Reported content")
    dup = reports_collection.find_one({"reporter_id": user["_id"], "content_type": data.content_type, "content_id": cid, "status": "pending"})
    if dup:
        raise HTTPException(409, "You already reported this and it is under review")
    ts = now()
    doc = {
        "reporter_id": user["_id"],
        "content_type": data.content_type,
        "content_id": cid,
        "reason": data.reason.strip(),
        "status": "pending",
        "resolution": None,
        "created_at": ts,
        "updated_at": ts,
    }
    res = reports_collection.insert_one(doc)
    for admin in users_collection.find({"role": "admin", "is_active": True}):
        notify(admin["_id"], "New report", f"A {data.content_type} was reported.", "system", "/admin/reports")
    return {"message": "Thanks - our team will review this report", "report_id": str(res.inserted_id)}


# ------------------------------------------------- banners + stats


@router.get("/banners")
def public_banners():
    return [ser(b) for b in banners_collection.find({"is_active": True}).sort("created_at", -1).limit(10)]


@router.get("/public/stats")
def public_stats():
    return {
        "jobs": jobs_collection.count_documents(public_visibility()),
        "companies": companies_collection.count_documents({"status": "approved"}),
        "candidates": users_collection.count_documents({"role": "user", "is_active": True}),
    }
