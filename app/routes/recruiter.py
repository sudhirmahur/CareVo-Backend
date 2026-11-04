"""Recruiter panel API (admins may also use these endpoints for any company)."""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import (
    applications_collection,
    companies_collection,
    followers_collection,
    interviews_collection,
    jobs_collection,
    users_collection,
    videos_collection,
)
from app.dependencies.auth import require_recruiter
from app.schemas.jobs import (
    ApplicationStatusUpdate,
    CompanyIn,
    CompanyUpdate,
    InterviewIn,
    InterviewUpdate,
    JobIn,
    JobUpdate,
)
from app.services.applications import shape_applications
from app.services.jobs import (
    apply_job_update,
    build_job_document,
    shape_jobs,
    sync_job_skills,
)
from app.services.notify import notify
from app.services.settings import get_settings
from app.utils import get_or_404, now, oid, paginate, rx, ser

router = APIRouter()


# ------------------------------------------------------------ company


def my_company(user: dict) -> Optional[dict]:
    return companies_collection.find_one({"recruiter_id": user["_id"]})


@router.get("/company")
def get_company(user=Depends(require_recruiter)):
    company = my_company(user)
    if not company:
        raise HTTPException(404, "Company profile not created yet")
    out = ser(company)
    out["followers_count"] = followers_collection.count_documents({"following_id": company["_id"], "type": "company"})
    out["jobs_count"] = jobs_collection.count_documents({"company_id": company["_id"]})
    return out


@router.post("/company", status_code=201)
def create_company(data: CompanyIn, user=Depends(require_recruiter)):
    if my_company(user):
        raise HTTPException(409, "You already have a company profile")
    ts = now()
    approved = get_settings()["auto_approve_companies"] or user["role"] == "admin"
    doc = {
        **data.model_dump(),
        "recruiter_id": user["_id"],
        "is_verified": False,
        "status": "approved" if approved else "pending",
        "created_at": ts,
        "updated_at": ts,
    }
    doc["_id"] = companies_collection.insert_one(dict(doc)).inserted_id
    return ser(doc)


@router.put("/company")
def update_company(data: CompanyUpdate, user=Depends(require_recruiter)):
    company = my_company(user)
    if not company:
        raise HTTPException(404, "Company profile not created yet")
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No data provided")
    values["updated_at"] = now()
    companies_collection.update_one({"_id": company["_id"]}, {"$set": values})
    return ser(companies_collection.find_one({"_id": company["_id"]}))


# --------------------------------------------------------------- jobs


def _own_job(user, job_id: str) -> dict:
    extra = {} if user["role"] == "admin" else {"recruiter_id": user["_id"]}
    return get_or_404(jobs_collection, oid(job_id, "job ID"), "Job", extra)


@router.get("/jobs")
def list_my_jobs(
    search: Optional[str] = None,
    status: Optional[str] = Query(default=None, pattern="^(pending|approved|rejected)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=50),
    user=Depends(require_recruiter),
):
    query: dict = {"recruiter_id": user["_id"]}
    if search:
        query["title"] = rx(search)
    if status:
        query["status"] = status
    result = paginate(jobs_collection, query, page, limit, [("created_at", -1)], mapper=lambda d: d)
    result["items"] = shape_jobs(result["items"], None)
    return result


@router.post("/jobs", status_code=201)
def create_job(data: JobIn, user=Depends(require_recruiter)):
    company = my_company(user)
    if not company:
        raise HTTPException(400, "Create your company profile before posting jobs")
    if company.get("status") == "rejected":
        raise HTTPException(403, "Your company profile was rejected. Update it or contact support.")
    status = "approved" if get_settings()["auto_approve_jobs"] and company.get("status") == "approved" else "pending"
    doc = build_job_document(data, company, user["_id"], status)
    doc["_id"] = jobs_collection.insert_one(dict(doc)).inserted_id
    sync_job_skills(doc["_id"], doc["skills"])
    if status == "pending":
        for admin in users_collection.find({"role": "admin", "is_active": True}):
            notify(admin["_id"], "Job awaiting approval", f"{doc['title']} by {company['name']}", "job", "/admin/jobs")
    return shape_jobs([doc])[0]


@router.get("/jobs/{job_id}")
def get_my_job(job_id: str, user=Depends(require_recruiter)):
    return shape_jobs([_own_job(user, job_id)])[0]


@router.put("/jobs/{job_id}")
def update_job(job_id: str, data: JobUpdate, user=Depends(require_recruiter)):
    job = _own_job(user, job_id)
    values = apply_job_update(job, data)
    # a rejected job goes back for review once the recruiter edits it
    if job.get("status") == "rejected":
        values["status"] = "pending"
        values["rejection_reason"] = None
    jobs_collection.update_one({"_id": job["_id"]}, {"$set": values})
    if "skills" in values:
        sync_job_skills(job["_id"], values["skills"])
    return shape_jobs([jobs_collection.find_one({"_id": job["_id"]})])[0]


@router.patch("/jobs/{job_id}/toggle")
def toggle_job(job_id: str, user=Depends(require_recruiter)):
    job = _own_job(user, job_id)
    new_state = not job.get("is_active", True)
    jobs_collection.update_one({"_id": job["_id"]}, {"$set": {"is_active": new_state, "updated_at": now()}})
    return {"is_active": new_state, "message": "Job activated" if new_state else "Job paused"}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, user=Depends(require_recruiter)):
    from app.services.jobs import delete_job_cascade

    job = _own_job(user, job_id)
    delete_job_cascade(job["_id"])
    return {"message": "Job deleted"}


# ------------------------------------------------------- applications


def _app_scope(user) -> dict:
    return {} if user["role"] == "admin" else {"recruiter_id": user["_id"]}


@router.get("/applications")
def list_applications(
    job_id: Optional[str] = None,
    status: Optional[str] = Query(default=None, pattern="^(applied|shortlisted|rejected|accepted|withdrawn)$"),
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user=Depends(require_recruiter),
):
    query = _app_scope(user)
    if job_id:
        query["job_id"] = oid(job_id, "job ID")
    if status:
        query["status"] = status
    if search:
        ids = [u["_id"] for u in users_collection.find({"$or": [{"name": rx(search)}, {"email": rx(search)}]})]
        query["user_id"] = {"$in": ids}
    result = paginate(applications_collection, query, page, limit, [("applied_at", -1)], mapper=lambda d: d)
    result["items"] = shape_applications(result["items"], "recruiter")
    return result


@router.get("/applications/{application_id}")
def get_application(application_id: str, user=Depends(require_recruiter)):
    app = get_or_404(
        applications_collection, oid(application_id, "application ID"), "Application", _app_scope(user)
    )
    return shape_applications([app], "recruiter")[0]


@router.patch("/applications/{application_id}/status")
def update_application_status(
    application_id: str, data: ApplicationStatusUpdate, user=Depends(require_recruiter)
):
    app = get_or_404(
        applications_collection, oid(application_id, "application ID"), "Application", _app_scope(user)
    )
    if app["status"] == "withdrawn":
        raise HTTPException(400, "The candidate withdrew this application")
    ts = now()
    applications_collection.update_one(
        {"_id": app["_id"]},
        {
            "$set": {"status": data.status, "updated_at": ts},
            "$push": {"status_history": {"status": data.status, "at": ts, "note": data.note}},
        },
    )
    if data.status != app["status"]:
        titles = {
            "shortlisted": "You have been shortlisted",
            "rejected": "Application update",
            "accepted": "Congratulations! You got selected",
            "applied": "Application moved back to review",
        }
        notify(
            app["user_id"],
            titles[data.status],
            f"Your application for {app.get('job_title')} at {app.get('company_name') or 'the company'} is now '{data.status}'."
            + (f" Note: {data.note}" if data.note else ""),
            "application",
            "/applications",
        )
    return {"message": "Status updated", "status": data.status}


# --------------------------------------------------------- interviews


def _interview_scope(user) -> dict:
    return {} if user["role"] == "admin" else {"recruiter_id": user["_id"]}


def _shape_interviews(rows: list) -> list:
    app_map = {a["_id"]: a for a in applications_collection.find({"_id": {"$in": [r["application_id"] for r in rows]}})}
    users = {u["_id"]: u for u in users_collection.find({"_id": {"$in": [a["user_id"] for a in app_map.values()]}})}
    out = []
    for r in rows:
        item = ser(r)
        a = app_map.get(r["application_id"])
        item["job_title"] = a.get("job_title") if a else None
        item["company_name"] = a.get("company_name") if a else None
        cand = users.get(a["user_id"]) if a else None
        item["candidate"] = {"id": str(cand["_id"]), "name": cand["name"], "email": cand["email"]} if cand else None
        out.append(item)
    return out


@router.get("/interviews")
def list_interviews(
    status: Optional[str] = Query(default=None, pattern="^(scheduled|completed|cancelled)$"),
    user=Depends(require_recruiter),
):
    query = _interview_scope(user)
    if status:
        query["status"] = status
    rows = list(interviews_collection.find(query).sort("scheduled_at", 1))
    return {"items": _shape_interviews(rows), "total": len(rows)}


@router.post("/interviews", status_code=201)
def schedule_interview(data: InterviewIn, user=Depends(require_recruiter)):
    app = get_or_404(
        applications_collection, oid(data.application_id, "application ID"), "Application", _app_scope(user)
    )
    if app["status"] in ("rejected", "withdrawn"):
        raise HTTPException(400, f"Cannot schedule an interview for a {app['status']} application")
    if data.mode == "online" and not data.meeting_link:
        raise HTTPException(422, "A meeting link is required for online interviews")
    ts = now()
    doc = {
        "application_id": app["_id"],
        "recruiter_id": app.get("recruiter_id") or user["_id"],
        "candidate_id": app["user_id"],
        "scheduled_at": data.scheduled_at,
        "mode": data.mode,
        "meeting_link": data.meeting_link,
        "location": data.location,
        "status": "scheduled",
        "feedback": None,
        "created_at": ts,
        "updated_at": ts,
    }
    doc["_id"] = interviews_collection.insert_one(dict(doc)).inserted_id
    if app["status"] == "applied":
        applications_collection.update_one(
            {"_id": app["_id"]},
            {
                "$set": {"status": "shortlisted", "updated_at": ts},
                "$push": {"status_history": {"status": "shortlisted", "at": ts, "note": "Interview scheduled"}},
            },
        )
    notify(
        app["user_id"],
        "Interview scheduled",
        f"Interview for {app.get('job_title')} on {data.scheduled_at:%d %b %Y, %H:%M} ({data.mode}).",
        "application",
        "/interviews",
    )
    return _shape_interviews([doc])[0]


@router.put("/interviews/{interview_id}")
def update_interview(interview_id: str, data: InterviewUpdate, user=Depends(require_recruiter)):
    iv = get_or_404(interviews_collection, oid(interview_id, "interview ID"), "Interview", _interview_scope(user))
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No data provided")
    values["updated_at"] = now()
    interviews_collection.update_one({"_id": iv["_id"]}, {"$set": values})
    if values.get("status") == "cancelled" or "scheduled_at" in values:
        notify(
            iv["candidate_id"],
            "Interview updated",
            "Your interview was cancelled." if values.get("status") == "cancelled" else "Your interview time was changed.",
            "application",
            "/interviews",
        )
    return _shape_interviews([interviews_collection.find_one({"_id": iv["_id"]})])[0]


# ---------------------------------------------------- followers/dashboard


@router.get("/followers")
def followers(user=Depends(require_recruiter)):
    company = my_company(user)
    if not company:
        return {"items": [], "total": 0}
    rows = list(followers_collection.find({"following_id": company["_id"], "type": "company"}).sort("created_at", -1))
    people = {u["_id"]: u for u in users_collection.find({"_id": {"$in": [r["follower_id"] for r in rows]}})}
    items = [
        {"id": str(r["follower_id"]), "name": people[r["follower_id"]]["name"], "email": people[r["follower_id"]]["email"], "followed_at": ser(r["created_at"])}
        for r in rows
        if r["follower_id"] in people
    ]
    return {"items": items, "total": len(items)}


@router.get("/dashboard")
def dashboard(user=Depends(require_recruiter)):
    company = my_company(user)
    scope = _app_scope(user)
    my_jobs = list(jobs_collection.find({"recruiter_id": user["_id"]} if user["role"] != "admin" else {}))
    apps = list(applications_collection.find(scope))
    by_status = {"applied": 0, "shortlisted": 0, "rejected": 0, "accepted": 0, "withdrawn": 0}
    for a in apps:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
    # applications per day, last 7 days
    today = now().date()
    days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    per_day = {d.isoformat(): 0 for d in days}
    for a in apps:
        key = a["applied_at"].date().isoformat()
        if key in per_day:
            per_day[key] += 1
    top = sorted(my_jobs, key=lambda j: j.get("applications_count", 0), reverse=True)[:5]
    return {
        "company": ser(company) if company else None,
        "jobs_total": len(my_jobs),
        "jobs_active": sum(1 for j in my_jobs if j.get("is_active") and j.get("status") == "approved"),
        "jobs_pending": sum(1 for j in my_jobs if j.get("status") == "pending"),
        "views_total": sum(j.get("views_count", 0) for j in my_jobs),
        "applications_total": len(apps),
        "applications_by_status": by_status,
        "interviews_upcoming": interviews_collection.count_documents(
            {**_interview_scope(user), "status": "scheduled", "scheduled_at": {"$gte": now()}}
        ),
        "followers": followers_collection.count_documents({"following_id": company["_id"], "type": "company"}) if company else 0,
        "videos": videos_collection.count_documents({"user_id": user["_id"]}),
        "applications_last_7_days": [{"date": d, "count": c} for d, c in per_day.items()],
        "top_jobs": [
            {"id": str(j["_id"]), "title": j["title"], "views": j.get("views_count", 0), "applications": j.get("applications_count", 0)}
            for j in top
        ],
    }
