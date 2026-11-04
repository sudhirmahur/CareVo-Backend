"""Shared job helpers: skill sync, response shaping, visibility rules."""
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from app.database import (
    applications_collection,
    companies_collection,
    job_skills_collection,
    saved_jobs_collection,
    skills_collection,
)
from app.utils import clean_list, now, ser


def public_visibility() -> dict:
    """Jobs candidates are allowed to see."""
    return {"status": "approved", "is_active": True}


def is_expired(job: dict) -> bool:
    deadline: Optional[datetime] = job.get("application_deadline")
    if not deadline:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline < now()


def sync_job_skills(job_id: ObjectId, skill_names: list) -> list:
    """Keep the job_skills join table in step with the job's skill list."""
    names = clean_list(skill_names)
    job_skills_collection.delete_many({"job_id": job_id})
    for name in names:
        existing = skills_collection.find_one({"name": {"$regex": f"^{__import__('re').escape(name)}$", "$options": "i"}})
        if existing:
            skill_id = existing["_id"]
        else:
            ts = now()
            skill_id = skills_collection.insert_one(
                {"name": name, "category_id": None, "created_at": ts, "updated_at": ts}
            ).inserted_id
        job_skills_collection.insert_one(
            {"job_id": job_id, "skill_id": skill_id, "is_required": True, "created_at": now()}
        )
    return names


def company_snapshot(company: Optional[dict]) -> Optional[dict]:
    if not company:
        return None
    return {
        "id": str(company["_id"]),
        "name": company.get("name"),
        "logo_url": company.get("logo_url"),
        "website": company.get("website"),
        "industry": company.get("industry"),
        "location": company.get("location"),
        "is_verified": company.get("is_verified", False),
    }


def shape_jobs(jobs: list, user: Optional[dict] = None, full: bool = False) -> list:
    """Attach company info and (for a signed-in user) saved/applied flags."""
    company_ids = {j["company_id"] for j in jobs if j.get("company_id")}
    companies = {c["_id"]: c for c in companies_collection.find({"_id": {"$in": list(company_ids)}})}
    saved, applied = set(), {}
    if user is not None and jobs:
        job_ids = [j["_id"] for j in jobs]
        saved = {s["job_id"] for s in saved_jobs_collection.find({"user_id": user["_id"], "job_id": {"$in": job_ids}})}
        applied = {
            a["job_id"]: a
            for a in applications_collection.find({"user_id": user["_id"], "job_id": {"$in": job_ids}})
        }
    out = []
    for job in jobs:
        company = company_snapshot(companies.get(job.get("company_id")))
        item = ser(job)
        item["company"] = company
        item["company_name"] = company["name"] if company else None
        item["company_logo"] = company["logo_url"] if company else None
        item["apply_type"] = "external" if job.get("apply_url") else "internal"
        item["is_expired"] = is_expired(job)
        if user is not None:
            item["is_saved"] = job["_id"] in saved
            app = applied.get(job["_id"])
            item["has_applied"] = app is not None
            item["application_status"] = app["status"] if app else None
            item["application_id"] = str(app["_id"]) if app else None
        out.append(item)
    return out


def build_job_document(data, company: dict, recruiter_id, status: str) -> dict:
    ts = now()
    return {
        "company_id": company["_id"],
        "recruiter_id": recruiter_id,
        "title": data.title.strip(),
        "description": data.description.strip(),
        "requirements": clean_list(data.requirements),
        "responsibilities": clean_list(data.responsibilities),
        "skills": clean_list(data.skills),
        "job_type": data.job_type,
        "salary_min": data.salary_min,
        "salary_max": data.salary_max,
        "location": data.location.strip(),
        "apply_url": data.apply_url,
        "application_deadline": data.application_deadline,
        "is_active": data.is_active,
        "is_featured": False,
        "status": status,  # pending | approved | rejected
        "rejection_reason": None,
        "views_count": 0,
        "applications_count": 0,
        "created_at": ts,
        "updated_at": ts,
    }


def apply_job_update(job: dict, data) -> dict:
    """Return the $set dict for a JobUpdate."""
    values = data.model_dump(exclude_unset=True)
    clear = values.pop("clear_apply_url", False)
    if clear:
        values["apply_url"] = None
    for key in ("requirements", "responsibilities", "skills"):
        if key in values and values[key] is not None:
            values[key] = clean_list(values[key])
    for key in ("title", "description", "location"):
        if key in values and values[key] is not None:
            values[key] = values[key].strip()
    lo = values.get("salary_min", job.get("salary_min"))
    hi = values.get("salary_max", job.get("salary_max"))
    if lo is not None and hi is not None and hi < lo:
        from fastapi import HTTPException

        raise HTTPException(422, "salary_max must be greater than or equal to salary_min")
    values["updated_at"] = now()
    return values


def delete_job_cascade(job_id) -> None:
    """Remove a job and its dependent rows (applications are kept as history snapshots)."""
    from app.database import jobs_collection

    job_skills_collection.delete_many({"job_id": job_id})
    saved_jobs_collection.delete_many({"job_id": job_id})
    jobs_collection.delete_one({"_id": job_id})
