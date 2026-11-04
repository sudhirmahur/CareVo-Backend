"""Public job board + candidate actions (save / apply)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import (
    applications_collection,
    companies_collection,
    jobs_collection,
    resumes_collection,
    saved_jobs_collection,
    user_profiles_collection,
    user_skills_collection,
    skills_collection,
)
from app.dependencies.auth import get_current_user, get_optional_user, require_roles
from app.schemas.jobs import ApplicationCreate
from app.services.jobs import is_expired, public_visibility, shape_jobs
from app.services.notify import notify
from app.utils import clean_list, get_or_404, now, oid, rx, ser

router = APIRouter()


def build_job_query(
    search: Optional[str] = None,
    location: Optional[str] = None,
    job_type: Optional[str] = None,
    skills: Optional[str] = None,
    min_salary: Optional[int] = None,
    company: Optional[str] = None,
    featured: Optional[bool] = None,
    include_expired: bool = False,
) -> dict:
    query: dict = dict(public_visibility())
    ands: list = []
    if search:
        pattern = rx(search)
        ands.append(
            {"$or": [{"title": pattern}, {"description": pattern}, {"skills": pattern}, {"location": pattern}]}
        )
    if location:
        query["location"] = rx(location)
    if job_type:
        query["job_type"] = job_type
    if skills:
        wanted = clean_list(skills.split(","))
        if wanted:
            ands.append({"$or": [{"skills": rx(s)} for s in wanted]})
    if min_salary:
        ands.append({"$or": [{"salary_max": {"$gte": min_salary}}, {"salary_min": {"$gte": min_salary}}]})
    if company:
        ids = [c["_id"] for c in companies_collection.find({"name": rx(company)})]
        query["company_id"] = {"$in": ids}
    if featured is not None:
        query["is_featured"] = featured
    if not include_expired:
        ands.append(
            {"$or": [{"application_deadline": None}, {"application_deadline": {"$gte": now()}}]}
        )
    if ands:
        query["$and"] = ands
    return query


@router.get("")
def list_jobs(
    search: Optional[str] = None,
    location: Optional[str] = None,
    job_type: Optional[str] = Query(default=None, pattern="^(fulltime|parttime|internship|contract)$"),
    skills: Optional[str] = None,
    min_salary: Optional[int] = Query(default=None, ge=0),
    company: Optional[str] = None,
    featured: Optional[bool] = None,
    sort: str = Query(default="latest", pattern="^(latest|salary|featured)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=50),
    user=Depends(get_optional_user),
):
    query = build_job_query(search, location, job_type, skills, min_salary, company, featured)
    total = jobs_collection.count_documents(query)
    sort_spec = {
        "latest": [("created_at", -1)],
        "salary": [("salary_max", -1), ("created_at", -1)],
        "featured": [("is_featured", -1), ("created_at", -1)],
    }[sort]
    docs = list(jobs_collection.find(query).sort(sort_spec).skip((page - 1) * limit).limit(limit))
    return {
        "items": shape_jobs(docs, user),
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
    }


@router.get("/recommended")
def recommended(user=Depends(get_current_user), limit: int = Query(default=6, ge=1, le=20)):
    profile = user_profiles_collection.find_one({"user_id": user["_id"]}) or {}
    names = list(profile.get("skills", []))
    rows = list(user_skills_collection.find({"user_id": user["_id"]}))
    if rows:
        names += [s["name"] for s in skills_collection.find({"_id": {"$in": [r["skill_id"] for r in rows]}})]
    names = clean_list(names)
    applied_ids = [a["job_id"] for a in applications_collection.find({"user_id": user["_id"]})]
    base = build_job_query()
    base["_id"] = {"$nin": applied_ids}
    pool = list(jobs_collection.find(base).sort("created_at", -1).limit(100))
    wanted = {n.lower() for n in names}

    def score(job):
        return len(wanted & {s.lower() for s in job.get("skills", [])})

    pool.sort(key=lambda j: (score(j), j.get("is_featured", False)), reverse=True)
    picked = pool[:limit]
    items = shape_jobs(picked, user)
    for item, job in zip(items, picked):
        item["match_score"] = score(job)
    return {"items": items, "skills_used": names}


@router.get("/saved")
def saved_jobs(user=Depends(get_current_user)):
    rows = list(saved_jobs_collection.find({"user_id": user["_id"]}).sort("created_at", -1))
    jobs = {j["_id"]: j for j in jobs_collection.find({"_id": {"$in": [r["job_id"] for r in rows]}})}
    ordered = [jobs[r["job_id"]] for r in rows if r["job_id"] in jobs]
    return {"items": shape_jobs(ordered, user), "total": len(ordered)}


@router.get("/{job_id}")
def get_job(job_id: str, user=Depends(get_optional_user)):
    _id = oid(job_id, "job ID")
    job = jobs_collection.find_one({"_id": _id})
    owner_view = bool(
        user and (user["role"] == "admin" or (job and job.get("recruiter_id") == user["_id"]))
    )
    if not job or (not owner_view and (job.get("status") != "approved" or not job.get("is_active"))):
        raise HTTPException(404, "Job not found")
    jobs_collection.update_one({"_id": _id}, {"$inc": {"views_count": 1}})
    job["views_count"] = job.get("views_count", 0) + 1
    return shape_jobs([job], user, full=True)[0]


@router.post("/{job_id}/save", status_code=201)
def save_job(job_id: str, user=Depends(get_current_user)):
    _id = oid(job_id, "job ID")
    get_or_404(jobs_collection, _id, "Job", public_visibility())
    saved_jobs_collection.update_one(
        {"user_id": user["_id"], "job_id": _id},
        {"$setOnInsert": {"user_id": user["_id"], "job_id": _id, "created_at": now()}},
        upsert=True,
    )
    return {"message": "Job saved", "is_saved": True}


@router.delete("/{job_id}/save")
def unsave_job(job_id: str, user=Depends(get_current_user)):
    saved_jobs_collection.delete_one({"user_id": user["_id"], "job_id": oid(job_id, "job ID")})
    return {"message": "Job removed from saved", "is_saved": False}


@router.post("/{job_id}/apply", status_code=201)
def apply_for_job(job_id: str, data: ApplicationCreate, user=Depends(require_roles("user"))):
    """Apply to a job.

    * Job has an `apply_url` (employer's own page): the application is recorded in
      the candidate's history immediately and `redirect_url` tells the client where
      to send the candidate to finish on the employer's site.
    * No `apply_url`: a normal in-platform application that needs a resume.
    Re-applying is safe: it returns `already_applied: true` (and the link again).
    """
    _id = oid(job_id, "job ID")
    job = get_or_404(jobs_collection, _id, "Job", public_visibility())
    if is_expired(job):
        raise HTTPException(400, "The application deadline for this job has passed")

    apply_url = job.get("apply_url")
    existing = applications_collection.find_one({"job_id": _id, "user_id": user["_id"]})
    if existing:
        return {
            "message": "You have already applied for this job",
            "already_applied": True,
            "application_id": str(existing["_id"]),
            "status": existing["status"],
            "redirect_url": apply_url,
            "source": existing.get("source", "internal"),
        }

    resume_id = None
    if data.resume_id:
        resume = get_or_404(
            resumes_collection, oid(data.resume_id, "resume ID"), "Resume", {"user_id": user["_id"]}
        )
        resume_id = resume["_id"]
    else:
        default = resumes_collection.find_one({"user_id": user["_id"], "is_default": True}) or resumes_collection.find_one(
            {"user_id": user["_id"]}
        )
        resume_id = default["_id"] if default else None
    if not apply_url and resume_id is None:
        raise HTTPException(400, "Please upload a resume before applying")

    company = companies_collection.find_one({"_id": job.get("company_id")}) or {}
    ts = now()
    doc = {
        "job_id": _id,
        "user_id": user["_id"],
        "company_id": job.get("company_id"),
        "recruiter_id": job.get("recruiter_id"),
        "resume_id": resume_id,
        "cover_letter": (data.cover_letter or "").strip() or None,
        "status": "applied",
        "source": "external" if apply_url else "internal",
        "apply_url": apply_url,
        "job_title": job.get("title"),
        "company_name": company.get("name"),
        "status_history": [{"status": "applied", "at": ts, "note": None}],
        "applied_at": ts,
        "updated_at": ts,
    }
    result = applications_collection.insert_one(doc)
    jobs_collection.update_one({"_id": _id}, {"$inc": {"applications_count": 1}})
    if job.get("recruiter_id"):
        notify(
            job["recruiter_id"],
            "New application",
            f"{user['name']} applied for {job.get('title')}.",
            "application",
            "/recruiter/applications",
        )
    return {
        "message": "Application recorded" if not apply_url else "Application recorded - continue on the employer's site",
        "already_applied": False,
        "application_id": str(result.inserted_id),
        "status": "applied",
        "redirect_url": apply_url,
        "source": doc["source"],
    }
