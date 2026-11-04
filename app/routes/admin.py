"""Admin panel API - full control over users, recruiters, companies, jobs, content, settings."""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import (
    applications_collection,
    banners_collection,
    companies_collection,
    followers_collection,
    interviews_collection,
    jobs_collection,
    assessment_results_collection,
    messages_collection,
    notifications_collection,
    premium_requests_collection,
    prep_progress_collection,
    reports_collection,
    resume_builds_collection,
    resumes_collection,
    saved_jobs_collection,
    skill_categories_collection,
    skills_collection,
    user_profiles_collection,
    user_skills_collection,
    users_collection,
    video_comments_collection,
    videos_collection,
)
from app.dependencies.auth import require_admin
from app.models.users import create_profile_document, create_user_document
from app.routes.recruiter import _shape_interviews
from app.routes.videos import delete_video_cascade
from app.schemas.jobs import (
    AdminJobStatus,
    ApplicationStatusUpdate,
    JobIn,
    JobUpdate,
)
from app.schemas.social import (
    BannerIn,
    BroadcastIn,
    CategoryIn,
    ReportResolve,
    SettingsIn,
    SkillIn,
)
from app.schemas.users import AdminUserCreate, AdminUserUpdate
from app.services.applications import shape_applications
from app.services.auth import hash_password
from app.services.jobs import (
    apply_job_update,
    build_job_document,
    delete_job_cascade,
    shape_jobs,
    sync_job_skills,
)
from app.services.audit import audit
from app.services.notify import notify
from app.services.premium import is_premium
from app.services.settings import get_settings, update_settings
from app.services.videos import shape_videos
from app.utils import get_or_404, now, oid, paginate, rx, ser

router = APIRouter(dependencies=[Depends(require_admin)])


# ---------------------------------------------------------- analytics


@router.get("/stats")
def stats():
    ts = now()
    users_by_role = {"user": 0, "recruiter": 0, "admin": 0}
    for u in users_collection.find({}, {"role": 1}):
        users_by_role[u["role"]] = users_by_role.get(u["role"], 0) + 1
    apps_by_status: dict = {}
    for a in applications_collection.find({}, {"status": 1}):
        apps_by_status[a["status"]] = apps_by_status.get(a["status"], 0) + 1
    today = ts.date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    signups = {d: 0 for d in days}
    applies = {d: 0 for d in days}
    for u in users_collection.find({"created_at": {"$gte": ts - timedelta(days=15)}}, {"created_at": 1}):
        k = u["created_at"].date().isoformat()
        if k in signups:
            signups[k] += 1
    for a in applications_collection.find({"applied_at": {"$gte": ts - timedelta(days=15)}}, {"applied_at": 1}):
        k = a["applied_at"].date().isoformat()
        if k in applies:
            applies[k] += 1
    top = list(jobs_collection.find({}).sort("applications_count", -1).limit(5))
    return {
        "users_total": sum(users_by_role.values()),
        "users_by_role": users_by_role,
        "users_blocked": users_collection.count_documents({"is_active": False}),
        "companies_total": companies_collection.count_documents({}),
        "companies_pending": companies_collection.count_documents({"status": "pending"}),
        "jobs_total": jobs_collection.count_documents({}),
        "jobs_pending": jobs_collection.count_documents({"status": "pending"}),
        "jobs_approved": jobs_collection.count_documents({"status": "approved"}),
        "jobs_featured": jobs_collection.count_documents({"is_featured": True}),
        "applications_total": sum(apps_by_status.values()),
        "applications_by_status": apps_by_status,
        "interviews_total": interviews_collection.count_documents({}),
        "videos_total": videos_collection.count_documents({}),
        "reports_pending": reports_collection.count_documents({"status": "pending"}),
        "signups_last_14_days": [{"date": d, "count": c} for d, c in signups.items()],
        "applications_last_14_days": [{"date": d, "count": c} for d, c in applies.items()],
        "top_jobs": [
            {"id": str(j["_id"]), "title": j["title"], "applications": j.get("applications_count", 0), "views": j.get("views_count", 0)}
            for j in top
        ],
    }


# -------------------------------------------------------------- users


def _user_row(u: dict) -> dict:
    item = ser(u)
    for k in ("provider_id", "failed_logins", "locked_until", "tokens_valid_after"):
        item.pop(k, None)
    item["is_premium"] = is_premium(u) if u["role"] == "user" else False
    if u["role"] == "recruiter":
        c = companies_collection.find_one({"recruiter_id": u["_id"]}, {"name": 1, "status": 1})
        item["company"] = {"id": str(c["_id"]), "name": c["name"], "status": c.get("status")} if c else None
    return item


@router.get("/users")
def list_users(
    search: Optional[str] = None,
    role: Optional[str] = Query(default=None, pattern="^(user|recruiter|admin)$"),
    is_active: Optional[bool] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=15, ge=1, le=100),
):
    query: dict = {}
    if search:
        query["$or"] = [{"name": rx(search)}, {"email": rx(search)}]
    if role:
        query["role"] = role
    if is_active is not None:
        query["is_active"] = is_active
    return paginate(users_collection, query, page, limit, [("created_at", -1)], mapper=_user_row)


@router.post("/users", status_code=201)
def create_user(data: AdminUserCreate):
    if users_collection.find_one({"email": data.email}):
        raise HTTPException(409, "Email already registered")
    doc = create_user_document(data.name.strip(), data.email, hash_password(data.password), role=data.role)
    uid = users_collection.insert_one(dict(doc)).inserted_id
    user_profiles_collection.insert_one(create_profile_document(uid))
    doc["_id"] = uid
    return _user_row(doc)


@router.get("/users/{user_id}")
def get_user(user_id: str):
    u = get_or_404(users_collection, oid(user_id, "user ID"), "User")
    item = _user_row(u)
    item["profile"] = ser(user_profiles_collection.find_one({"user_id": u["_id"]}) or {})
    item["applications_count"] = applications_collection.count_documents({"user_id": u["_id"]})
    item["resumes_count"] = resumes_collection.count_documents({"user_id": u["_id"]})
    return item


@router.patch("/users/{user_id}")
def update_user(user_id: str, data: AdminUserUpdate, admin=Depends(require_admin)):
    u = get_or_404(users_collection, oid(user_id, "user ID"), "User")
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No data provided")
    if u["_id"] == admin["_id"] and (values.get("is_active") is False or values.get("role", "admin") != "admin"):
        raise HTTPException(400, "You cannot block or demote your own admin account")
    values["updated_at"] = now()
    if "role" in values and values["role"] != u["role"]:
        values["tokens_valid_after"] = now()  # old sessions must not keep the old privileges
    if values.get("is_active") is True:
        values.update(failed_logins=0, locked_until=None)
    users_collection.update_one({"_id": u["_id"]}, {"$set": values})
    audit(admin, "user.update", str(u["_id"]), ",".join(k for k in values if k != "updated_at"))
    if "is_active" in values and values["is_active"] != u.get("is_active", True):
        if values["is_active"] is False:
            # blocked recruiters should disappear from the job board
            jobs_collection.update_many({"recruiter_id": u["_id"]}, {"$set": {"is_active": False}})
    return _user_row(users_collection.find_one({"_id": u["_id"]}))


class PremiumUpdate(BaseModel):
    is_premium: bool
    days: Optional[int] = Field(default=None, ge=1, le=3650)  # None = no expiry


@router.patch("/users/{user_id}/premium")
def set_premium(user_id: str, data: PremiumUpdate, admin=Depends(require_admin)):
    u = get_or_404(users_collection, oid(user_id, "user ID"), "User")
    if u["role"] != "user":
        raise HTTPException(400, "Premium applies to candidate accounts only")
    until = now() + timedelta(days=data.days) if (data.is_premium and data.days) else None
    users_collection.update_one({"_id": u["_id"]}, {"$set": {"is_premium": data.is_premium, "premium_until": until, "updated_at": now()}})
    premium_requests_collection.update_many({"user_id": u["_id"], "status": "pending"}, {"$set": {"status": "granted" if data.is_premium else "closed", "resolved_at": now()}})
    notify(u["_id"], "Premium activated" if data.is_premium else "Premium ended", "You can now apply and view recruiter profiles from Job Reels." if data.is_premium else "Your Premium membership has ended.", "system", "/videos")
    audit(admin, "user.premium", str(u["_id"]), f"{data.is_premium} {data.days or 'no-expiry'}")
    return _user_row(users_collection.find_one({"_id": u["_id"]}))


@router.get("/premium-requests")
def premium_requests():
    rows = list(premium_requests_collection.find({"status": "pending"}).sort("created_at", 1))
    users = {x["_id"]: x for x in users_collection.find({"_id": {"$in": [r["user_id"] for r in rows]}})}
    items = [{"id": str(r["_id"]), "created_at": ser(r["created_at"]), "user": {"id": str(users[r["user_id"]]["_id"]), "name": users[r["user_id"]]["name"], "email": users[r["user_id"]]["email"]}} for r in rows if r["user_id"] in users]
    return {"items": items, "total": len(items)}


def delete_user_cascade(u: dict) -> None:
    uid = u["_id"]
    for job in list(jobs_collection.find({"recruiter_id": uid})):
        delete_job_cascade(job["_id"])
    companies_collection.delete_many({"recruiter_id": uid})
    for v in list(videos_collection.find({"user_id": uid})):
        delete_video_cascade(v)
    applications_collection.delete_many({"user_id": uid})
    resumes_collection.delete_many({"user_id": uid})
    saved_jobs_collection.delete_many({"user_id": uid})
    user_skills_collection.delete_many({"user_id": uid})
    user_profiles_collection.delete_many({"user_id": uid})
    notifications_collection.delete_many({"user_id": uid})
    messages_collection.delete_many({"$or": [{"sender_id": uid}, {"receiver_id": uid}]})
    followers_collection.delete_many({"$or": [{"follower_id": uid}, {"following_id": uid}]})
    video_comments_collection.delete_many({"user_id": uid})
    resume_builds_collection.delete_many({"user_id": uid})
    assessment_results_collection.delete_many({"user_id": uid})
    prep_progress_collection.delete_many({"user_id": uid})
    premium_requests_collection.delete_many({"user_id": uid})
    users_collection.delete_one({"_id": uid})


@router.delete("/users/{user_id}")
def delete_user(user_id: str, admin=Depends(require_admin)):
    u = get_or_404(users_collection, oid(user_id, "user ID"), "User")
    if u["_id"] == admin["_id"]:
        raise HTTPException(400, "You cannot delete your own account")
    delete_user_cascade(u)
    audit(admin, "user.delete", str(u["_id"]), u.get("email"))
    return {"message": "User and related data deleted"}


# ---------------------------------------------------------- companies


@router.get("/companies")
def list_companies(
    search: Optional[str] = None,
    status: Optional[str] = Query(default=None, pattern="^(pending|approved|rejected)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=15, ge=1, le=100),
):
    query: dict = {}
    if search:
        query["name"] = rx(search)
    if status:
        query["status"] = status

    def row(c):
        item = ser(c)
        owner = users_collection.find_one({"_id": c["recruiter_id"]}, {"name": 1, "email": 1})
        item["recruiter"] = {"id": str(owner["_id"]), "name": owner["name"], "email": owner["email"]} if owner else None
        item["jobs_count"] = jobs_collection.count_documents({"company_id": c["_id"]})
        return item

    return paginate(companies_collection, query, page, limit, [("created_at", -1)], mapper=row)


@router.patch("/companies/{company_id}")
def moderate_company(company_id: str, body: dict):
    c = get_or_404(companies_collection, oid(company_id, "company ID"), "Company")
    allowed = {}
    if "status" in body:
        if body["status"] not in ("pending", "approved", "rejected"):
            raise HTTPException(422, "Invalid status")
        allowed["status"] = body["status"]
    if "is_verified" in body:
        allowed["is_verified"] = bool(body["is_verified"])
    if not allowed:
        raise HTTPException(400, "Nothing to update")
    allowed["updated_at"] = now()
    companies_collection.update_one({"_id": c["_id"]}, {"$set": allowed})
    if "status" in allowed and allowed["status"] != c.get("status"):
        notify(c["recruiter_id"], f"Company {allowed['status']}", f"Your company '{c['name']}' was {allowed['status']} by an admin.", "system", "/recruiter/company")
    return ser(companies_collection.find_one({"_id": c["_id"]}))


@router.delete("/companies/{company_id}")
def delete_company(company_id: str):
    c = get_or_404(companies_collection, oid(company_id, "company ID"), "Company")
    for job in list(jobs_collection.find({"company_id": c["_id"]})):
        delete_job_cascade(job["_id"])
    followers_collection.delete_many({"following_id": c["_id"]})
    companies_collection.delete_one({"_id": c["_id"]})
    return {"message": "Company and its jobs deleted"}


# --------------------------------------------------------------- jobs


@router.get("/jobs")
def list_jobs(
    search: Optional[str] = None,
    status: Optional[str] = Query(default=None, pattern="^(pending|approved|rejected)$"),
    featured: Optional[bool] = None,
    company_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=15, ge=1, le=100),
):
    query: dict = {}
    if search:
        query["$or"] = [{"title": rx(search)}, {"location": rx(search)}]
    if status:
        query["status"] = status
    if featured is not None:
        query["is_featured"] = featured
    if company_id:
        query["company_id"] = oid(company_id, "company ID")
    result = paginate(jobs_collection, query, page, limit, [("created_at", -1)], mapper=lambda d: d)
    result["items"] = shape_jobs(result["items"], None)
    return result


@router.post("/jobs", status_code=201)
def create_job(data: JobIn, admin=Depends(require_admin)):
    """Admins can post a job for any company (e.g. aggregated listings with an external apply link)."""
    if not data.company_id:
        raise HTTPException(422, "company_id is required")
    company = get_or_404(companies_collection, oid(data.company_id, "company ID"), "Company")
    doc = build_job_document(data, company, company["recruiter_id"], "approved")
    doc["_id"] = jobs_collection.insert_one(dict(doc)).inserted_id
    sync_job_skills(doc["_id"], doc["skills"])
    return shape_jobs([doc])[0]


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    return shape_jobs([get_or_404(jobs_collection, oid(job_id, "job ID"), "Job")])[0]


@router.put("/jobs/{job_id}")
def update_job(job_id: str, data: JobUpdate):
    job = get_or_404(jobs_collection, oid(job_id, "job ID"), "Job")
    values = apply_job_update(job, data)
    jobs_collection.update_one({"_id": job["_id"]}, {"$set": values})
    if "skills" in values:
        sync_job_skills(job["_id"], values["skills"])
    return shape_jobs([jobs_collection.find_one({"_id": job["_id"]})])[0]


@router.patch("/jobs/{job_id}/status")
def set_job_status(job_id: str, data: AdminJobStatus):
    job = get_or_404(jobs_collection, oid(job_id, "job ID"), "Job")
    values = {
        "status": data.status,
        "rejection_reason": data.reason if data.status == "rejected" else None,
        "updated_at": now(),
    }
    jobs_collection.update_one({"_id": job["_id"]}, {"$set": values})
    if data.status != job.get("status") and job.get("recruiter_id"):
        text = {"approved": "was approved and is now live", "rejected": "was rejected", "pending": "was moved back to review"}[data.status]
        notify(
            job["recruiter_id"],
            f"Job {data.status}",
            f"Your job '{job['title']}' {text}." + (f" Reason: {data.reason}" if data.reason and data.status == "rejected" else ""),
            "job",
            "/recruiter/jobs",
        )
    return shape_jobs([jobs_collection.find_one({"_id": job["_id"]})])[0]


@router.patch("/jobs/{job_id}/feature")
def toggle_feature(job_id: str):
    job = get_or_404(jobs_collection, oid(job_id, "job ID"), "Job")
    new_state = not job.get("is_featured", False)
    jobs_collection.update_one({"_id": job["_id"]}, {"$set": {"is_featured": new_state, "updated_at": now()}})
    return {"is_featured": new_state}


@router.patch("/jobs/{job_id}/toggle")
def toggle_active(job_id: str):
    job = get_or_404(jobs_collection, oid(job_id, "job ID"), "Job")
    new_state = not job.get("is_active", True)
    jobs_collection.update_one({"_id": job["_id"]}, {"$set": {"is_active": new_state, "updated_at": now()}})
    return {"is_active": new_state}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    job = get_or_404(jobs_collection, oid(job_id, "job ID"), "Job")
    delete_job_cascade(job["_id"])
    return {"message": "Job deleted"}


# ------------------------------------------------ applications/interviews


@router.get("/applications")
def list_applications(
    status: Optional[str] = Query(default=None, pattern="^(applied|shortlisted|rejected|accepted|withdrawn)$"),
    job_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=15, ge=1, le=100),
):
    query: dict = {}
    if status:
        query["status"] = status
    if job_id:
        query["job_id"] = oid(job_id, "job ID")
    if search:
        ids = [u["_id"] for u in users_collection.find({"$or": [{"name": rx(search)}, {"email": rx(search)}]})]
        query["$or"] = [{"user_id": {"$in": ids}}, {"job_title": rx(search)}]
    result = paginate(applications_collection, query, page, limit, [("applied_at", -1)], mapper=lambda d: d)
    result["items"] = shape_applications(result["items"], "admin")
    return result


@router.patch("/applications/{application_id}/status")
def set_application_status(application_id: str, data: ApplicationStatusUpdate):
    app = get_or_404(applications_collection, oid(application_id, "application ID"), "Application")
    ts = now()
    applications_collection.update_one(
        {"_id": app["_id"]},
        {"$set": {"status": data.status, "updated_at": ts}, "$push": {"status_history": {"status": data.status, "at": ts, "note": data.note or "Updated by admin"}}},
    )
    if data.status != app["status"]:
        notify(app["user_id"], "Application update", f"Your application for {app.get('job_title')} is now '{data.status}'.", "application", "/applications")
    return {"message": "Status updated", "status": data.status}


@router.delete("/applications/{application_id}")
def delete_application(application_id: str):
    app = get_or_404(applications_collection, oid(application_id, "application ID"), "Application")
    interviews_collection.delete_many({"application_id": app["_id"]})
    applications_collection.delete_one({"_id": app["_id"]})
    jobs_collection.update_one({"_id": app["job_id"], "applications_count": {"$gt": 0}}, {"$inc": {"applications_count": -1}})
    return {"message": "Application deleted"}


@router.get("/interviews")
def list_interviews():
    rows = list(interviews_collection.find({}).sort("scheduled_at", -1).limit(200))
    return {"items": _shape_interviews(rows), "total": len(rows)}


# ----------------------------------------------------------- content


@router.get("/videos")
def list_videos(
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=12, ge=1, le=60),
):
    query: dict = {}
    if search:
        query["title"] = rx(search)
    result = paginate(videos_collection, query, page, limit, [("created_at", -1)], mapper=lambda d: d)
    result["items"] = shape_videos(result["items"], None)
    return result


@router.patch("/videos/{video_id}/moderate")
def moderate_video(video_id: str, body: dict):
    v = get_or_404(videos_collection, oid(video_id, "video ID"), "Video")
    values = {}
    if "is_removed" in body:
        values["is_removed"] = bool(body["is_removed"])
    if "is_public" in body:
        values["is_public"] = bool(body["is_public"])
    if not values:
        raise HTTPException(400, "Nothing to update")
    values["updated_at"] = now()
    videos_collection.update_one({"_id": v["_id"]}, {"$set": values})
    return shape_videos([videos_collection.find_one({"_id": v["_id"]})], None)[0]


@router.delete("/videos/{video_id}")
def delete_video(video_id: str):
    delete_video_cascade(get_or_404(videos_collection, oid(video_id, "video ID"), "Video"))
    return {"message": "Video deleted"}


# ----------------------------------------------------------- reports


@router.get("/reports")
def list_reports(
    status: Optional[str] = Query(default=None, pattern="^(pending|resolved)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=15, ge=1, le=100),
):
    query: dict = {}
    if status:
        query["status"] = status

    def row(r):
        item = ser(r)
        rep = users_collection.find_one({"_id": r["reporter_id"]}, {"name": 1, "email": 1})
        item["reporter"] = {"id": str(rep["_id"]), "name": rep["name"], "email": rep["email"]} if rep else None
        coll = {"job": jobs_collection, "user": users_collection, "video": videos_collection, "comment": video_comments_collection}[r["content_type"]]
        target = coll.find_one({"_id": r["content_id"]})
        item["target_exists"] = target is not None
        item["target_label"] = (
            (target.get("title") or target.get("name") or target.get("comment", "")[:60]) if target else "(removed)"
        )
        return item

    return paginate(reports_collection, query, page, limit, [("created_at", -1)], mapper=row)


@router.patch("/reports/{report_id}")
def resolve_report(report_id: str, data: ReportResolve, admin=Depends(require_admin)):
    r = get_or_404(reports_collection, oid(report_id, "report ID"), "Report")
    note = data.note or ""
    if data.action == "remove_content":
        cid, ctype = r["content_id"], r["content_type"]
        if ctype == "job":
            delete_job_cascade(cid)
        elif ctype == "video":
            v = videos_collection.find_one({"_id": cid})
            if v:
                delete_video_cascade(v)
        elif ctype == "comment":
            c = video_comments_collection.find_one({"_id": cid})
            if c:
                video_comments_collection.delete_one({"_id": cid})
                videos_collection.update_one({"_id": c["video_id"]}, {"$set": {"comments_count": video_comments_collection.count_documents({"video_id": c["video_id"]})}})
        elif ctype == "user":
            raise HTTPException(400, "Use 'block_user' for reported users")
    elif data.action == "block_user":
        if r["content_type"] != "user":
            raise HTTPException(400, "block_user only applies to reported users")
        if r["content_id"] == admin["_id"]:
            raise HTTPException(400, "You cannot block yourself")
        users_collection.update_one({"_id": r["content_id"]}, {"$set": {"is_active": False, "updated_at": now()}})
    reports_collection.update_one(
        {"_id": r["_id"]},
        {"$set": {"status": data.status, "resolution": {"action": data.action, "note": note, "by": admin["_id"], "at": now()}, "updated_at": now()}},
    )
    return {"message": "Report updated", "status": data.status}


# ------------------------------------------- skills, banners, settings


@router.get("/skills")
def list_skills():
    cats = {c["_id"]: c["name"] for c in skill_categories_collection.find({})}
    out = []
    for s in skills_collection.find({}).sort("name", 1):
        item = ser(s)
        item["category"] = cats.get(s.get("category_id"))
        out.append(item)
    return {"items": out, "categories": [ser(c) for c in skill_categories_collection.find({}).sort("name", 1)]}


@router.post("/skills", status_code=201)
def create_skill(data: SkillIn):
    name = data.name.strip()
    if skills_collection.find_one({"name": {"$regex": f"^{__import__('re').escape(name)}$", "$options": "i"}}):
        raise HTTPException(409, "Skill already exists")
    cat = None
    if data.category_id:
        cat = get_or_404(skill_categories_collection, oid(data.category_id, "category ID"), "Category")["_id"]
    ts = now()
    doc = {"name": name, "category_id": cat, "created_at": ts, "updated_at": ts}
    doc["_id"] = skills_collection.insert_one(dict(doc)).inserted_id
    return ser(doc)


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: str):
    s = get_or_404(skills_collection, oid(skill_id, "skill ID"), "Skill")
    from app.database import job_skills_collection

    user_skills_collection.delete_many({"skill_id": s["_id"]})
    job_skills_collection.delete_many({"skill_id": s["_id"]})
    skills_collection.delete_one({"_id": s["_id"]})
    return {"message": "Skill deleted"}


@router.post("/skill-categories", status_code=201)
def create_category(data: CategoryIn):
    name = data.name.strip()
    if skill_categories_collection.find_one({"name": {"$regex": f"^{__import__('re').escape(name)}$", "$options": "i"}}):
        raise HTTPException(409, "Category already exists")
    ts = now()
    doc = {"name": name, "created_at": ts, "updated_at": ts}
    doc["_id"] = skill_categories_collection.insert_one(dict(doc)).inserted_id
    return ser(doc)


@router.delete("/skill-categories/{category_id}")
def delete_category(category_id: str):
    c = get_or_404(skill_categories_collection, oid(category_id, "category ID"), "Category")
    skills_collection.update_many({"category_id": c["_id"]}, {"$set": {"category_id": None}})
    skill_categories_collection.delete_one({"_id": c["_id"]})
    return {"message": "Category deleted"}


@router.get("/banners")
def list_banners():
    return {"items": [ser(b) for b in banners_collection.find({}).sort("created_at", -1)]}


@router.post("/banners", status_code=201)
def create_banner(data: BannerIn):
    doc = {**data.model_dump(), "created_at": now()}
    doc["_id"] = banners_collection.insert_one(dict(doc)).inserted_id
    return ser(doc)


@router.patch("/banners/{banner_id}")
def toggle_banner(banner_id: str):
    b = get_or_404(banners_collection, oid(banner_id, "banner ID"), "Banner")
    banners_collection.update_one({"_id": b["_id"]}, {"$set": {"is_active": not b.get("is_active", True)}})
    return {"is_active": not b.get("is_active", True)}


@router.delete("/banners/{banner_id}")
def delete_banner(banner_id: str):
    b = get_or_404(banners_collection, oid(banner_id, "banner ID"), "Banner")
    banners_collection.delete_one({"_id": b["_id"]})
    return {"message": "Banner deleted"}


@router.get("/settings")
def read_settings():
    return ser(get_settings())


@router.put("/settings")
def write_settings(data: SettingsIn):
    values = data.model_dump(exclude_unset=True, exclude_none=True)
    if not values:
        raise HTTPException(400, "No data provided")
    return ser(update_settings(values))


@router.post("/notifications", status_code=201)
def broadcast(data: BroadcastIn):
    query: dict = {"is_active": True}
    if data.audience != "all":
        query["role"] = data.audience
    count = 0
    for u in users_collection.find(query, {"_id": 1}):
        notify(u["_id"], data.title.strip(), data.message.strip(), "system")
        count += 1
    return {"message": f"Notification sent to {count} users", "recipients": count}
