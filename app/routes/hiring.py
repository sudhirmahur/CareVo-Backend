"""Recruiter hiring tools: evaluations, calendar, analytics, team, bulk candidates, offer letters, screening answers.

Mounted at /api/recruiter (alongside the core recruiter routes).
"""
import csv
import io
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.database import (
    applications_collection,
    calendar_events_collection,
    candidate_pool_collection,
    companies_collection,
    evaluations_collection,
    interviews_collection,
    job_views_collection,
    jobs_collection,
    offer_templates_collection,
    offer_assets_collection,
    offers_collection,
    team_members_collection,
    users_collection,
)
from app.dependencies.auth import require_recruiter
from app.routes.recruiter import _app_scope, _own_job, my_company
from app.schemas.common import HttpUrl, MediaUrl
from app.config import PUBLIC_APP_URL
from app.services import simplepdf, storage
from app.services.applications import shape_applications
from app.services.email import send_template
from app.services.notify import notify
from app.services.offers import DEFAULT_BODY, DEFAULT_TERMS, STYLES, letter_paragraphs, offer_out, render_pdf, styles_out
from app.utils import get_or_404, now, oid, ser

router = APIRouter()
files_router = APIRouter()


def _csv(rows: list[list], header: list[str]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        # neutralise spreadsheet formula injection
        w.writerow([("'" + c) if isinstance(c, str) and c[:1] in "=+-@" else c for c in r])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=export.csv"})


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _scope_app(user, application_id: str) -> dict:
    return get_or_404(applications_collection, oid(application_id, "application ID"), "Application", _app_scope(user))


def _company_name(user) -> str:
    c = my_company(user)
    return c["name"] if c else user.get("name", "Company")


# ======================================================================= screening answers
@router.get("/jobs/{job_id}/screening")
def screening_answers(
    job_id: str,
    question_id: Optional[str] = None,
    answer: Optional[str] = Query(default=None, max_length=300),
    user=Depends(require_recruiter),
):
    """Applicants with their screening answers; filter by one question's answer (case-insensitive)."""
    job = _own_job(user, job_id)
    questions = job.get("screening_questions", [])
    if question_id and question_id not in {q["id"] for q in questions}:
        raise HTTPException(404, "Question not found on this job")
    rows = list(applications_collection.find({"job_id": job["_id"]}).sort("applied_at", -1))
    if question_id and answer is not None:
        rows = [a for a in rows if any(x.get("question_id") == question_id and x.get("answer", "").strip().lower() == answer.strip().lower() for x in a.get("answers") or [])]
    items = shape_applications(rows, "recruiter")
    return {"questions": questions, "items": items, "total": len(items)}


# ======================================================================= evaluations
class EvaluationIn(BaseModel):
    application_id: str
    ratings: dict[str, int] = Field(default_factory=dict)
    recommendation: Literal["strong_yes", "yes", "no", "strong_no"]
    comment: Optional[str] = Field(default=None, max_length=2000)
    private_note: Optional[str] = Field(default=None, max_length=2000)
    reviewer: Optional[str] = Field(default=None, max_length=80)  # team member who gave the feedback
    stage: Optional[str] = Field(default="screening", max_length=40)


CRITERIA = ("technical", "communication", "problem_solving", "culture_fit", "experience")


def _eval_out(e: dict) -> dict:
    out = ser(e)
    vals = list(e.get("ratings", {}).values())
    out["average"] = round(sum(vals) / len(vals), 2) if vals else None
    return out


@router.get("/evaluation-criteria")
def evaluation_criteria(user=Depends(require_recruiter)):
    return {"criteria": list(CRITERIA), "scale": [1, 5], "recommendations": ["strong_yes", "yes", "no", "strong_no"]}


@router.post("/evaluations", status_code=201)
def create_evaluation(data: EvaluationIn, user=Depends(require_recruiter)):
    app = _scope_app(user, data.application_id)
    bad = [k for k, v in data.ratings.items() if k not in CRITERIA or not 1 <= v <= 5]
    if bad:
        raise HTTPException(422, f"Ratings must be 1-5 for: {', '.join(CRITERIA)}")
    if not data.ratings:
        raise HTTPException(422, "Add at least one rating")
    ts = now()
    doc = {
        "application_id": app["_id"],
        "job_id": app["job_id"],
        "candidate_id": app["user_id"],
        "recruiter_id": user["_id"],
        "reviewer": (data.reviewer or user["name"]).strip(),
        "stage": data.stage,
        "ratings": data.ratings,
        "recommendation": data.recommendation,
        "comment": data.comment,
        "private_note": data.private_note,
        "created_at": ts,
    }
    doc["_id"] = evaluations_collection.insert_one(dict(doc)).inserted_id
    return _eval_out(doc)


@router.get("/evaluations")
def list_evaluations(application_id: str, user=Depends(require_recruiter)):
    app = _scope_app(user, application_id)
    rows = list(evaluations_collection.find({"application_id": app["_id"]}).sort("created_at", -1))
    items = [_eval_out(r) for r in rows]
    avgs = [i["average"] for i in items if i["average"] is not None]
    return {"items": items, "total": len(items), "overall": round(sum(avgs) / len(avgs), 2) if avgs else None}


@router.delete("/evaluations/{eval_id}")
def delete_evaluation(eval_id: str, user=Depends(require_recruiter)):
    e = get_or_404(evaluations_collection, oid(eval_id, "evaluation ID"), "Evaluation", {} if user["role"] == "admin" else {"recruiter_id": user["_id"]})
    evaluations_collection.delete_one({"_id": e["_id"]})
    return {"message": "Evaluation deleted"}


@router.get("/evaluations/compare")
def compare(application_ids: str = Query(..., max_length=500), user=Depends(require_recruiter)):
    ids = [i for i in application_ids.split(",") if i][:5]
    if len(ids) < 2:
        raise HTTPException(422, "Pick at least two candidates to compare")
    apps = [_scope_app(user, i) for i in ids]
    shaped = {a["id"]: a for a in shape_applications(apps, "recruiter")}
    out = []
    for a in apps:
        evs = list(evaluations_collection.find({"application_id": a["_id"]}))
        per = {c: [] for c in CRITERIA}
        for e in evs:
            for k, v in e.get("ratings", {}).items():
                per[k].append(v)
        avg = {k: (round(sum(v) / len(v), 2) if v else None) for k, v in per.items()}
        got = [v for v in avg.values() if v is not None]
        s = shaped[str(a["_id"])]
        out.append(
            {
                "application_id": str(a["_id"]),
                "candidate": s.get("candidate"),
                "job_title": s.get("job_title"),
                "status": a["status"],
                "evaluations": len(evs),
                "criteria": avg,
                "overall": round(sum(got) / len(got), 2) if got else None,
                "recommendations": [e["recommendation"] for e in evs],
            }
        )
    return {"items": out, "criteria": list(CRITERIA)}


# ======================================================================= calendar
class CalendarIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    kind: Literal["interview", "meeting", "deadline", "reminder", "other"] = "meeting"
    starts_at: datetime
    ends_at: Optional[datetime] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    remind_minutes: int = Field(default=30, ge=0, le=10080)
    application_id: Optional[str] = None


class CalendarUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=120)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    remind_minutes: Optional[int] = Field(default=None, ge=0, le=10080)
    status: Optional[Literal["scheduled", "cancelled", "done"]] = None


def _own_event(user, event_id: str) -> dict:
    return get_or_404(calendar_events_collection, oid(event_id, "event ID"), "Event", {"recruiter_id": user["_id"]})


@router.post("/calendar", status_code=201)
def create_event(data: CalendarIn, user=Depends(require_recruiter)):
    if data.ends_at and _aware(data.ends_at) <= _aware(data.starts_at):
        raise HTTPException(422, "End time must be after the start time")
    app_id = None
    if data.application_id:
        app_id = _scope_app(user, data.application_id)["_id"]
    ts = now()
    doc = {**data.model_dump(exclude={"application_id"}), "application_id": app_id, "recruiter_id": user["_id"], "status": "scheduled", "reminded": False, "created_at": ts, "updated_at": ts}
    doc["_id"] = calendar_events_collection.insert_one(dict(doc)).inserted_id
    return ser(doc)


@router.patch("/calendar/{event_id}")
def update_event(event_id: str, data: CalendarUpdate, user=Depends(require_recruiter)):
    ev = _own_event(user, event_id)
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No data provided")
    start = values.get("starts_at", ev["starts_at"])
    end = values.get("ends_at", ev.get("ends_at"))
    if end and _aware(end) <= _aware(start):
        raise HTTPException(422, "End time must be after the start time")
    if "starts_at" in values or "remind_minutes" in values:
        values["reminded"] = False
    values["updated_at"] = now()
    calendar_events_collection.update_one({"_id": ev["_id"]}, {"$set": values})
    return ser(calendar_events_collection.find_one({"_id": ev["_id"]}))


@router.delete("/calendar/{event_id}")
def delete_event(event_id: str, user=Depends(require_recruiter)):
    ev = _own_event(user, event_id)
    calendar_events_collection.delete_one({"_id": ev["_id"]})
    return {"message": "Event deleted"}


def _range(view: str, day: datetime) -> tuple[datetime, datetime]:
    d0 = day.replace(hour=0, minute=0, second=0, microsecond=0)
    if view == "day":
        return d0, d0 + timedelta(days=1)
    if view == "week":
        start = d0 - timedelta(days=d0.weekday())
        return start, start + timedelta(days=7)
    start = d0.replace(day=1)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, nxt


@router.get("/calendar")
def calendar(
    view: Literal["day", "week", "month"] = "week",
    date: Optional[datetime] = None,
    user=Depends(require_recruiter),
):
    """Own events and scheduled interviews merged into one timeline."""
    start, end = _range(view, _aware(date) if date else datetime.now(timezone.utc))
    s, e = start, end
    items = []
    for ev in calendar_events_collection.find({"recruiter_id": user["_id"], "starts_at": {"$gte": s, "$lt": e}}):
        items.append({**ser(ev), "source": "event"})
    ivs = list(interviews_collection.find({"recruiter_id": user["_id"], "scheduled_at": {"$gte": s, "$lt": e}}))
    apps = {a["_id"]: a for a in applications_collection.find({"_id": {"$in": [i["application_id"] for i in ivs]}})}
    for iv in ivs:
        a = apps.get(iv["application_id"], {})
        items.append(
            {
                "id": str(iv["_id"]),
                "source": "interview",
                "kind": "interview",
                "title": f"Interview - {a.get('job_title', 'role')}",
                "starts_at": ser(iv)["scheduled_at"],
                "status": iv["status"],
                "mode": iv.get("mode"),
                "application_id": str(iv["application_id"]),
            }
        )
    items.sort(key=lambda x: str(x["starts_at"]))
    return {"view": view, "from": s.isoformat(), "to": e.isoformat(), "items": items, "total": len(items)}


def run_calendar_reminders() -> int:
    """Notify recruiters about events starting within their reminder window (called by the scheduler)."""
    n, ts = 0, now()
    for ev in calendar_events_collection.find({"status": "scheduled", "reminded": {"$ne": True}, "starts_at": {"$gte": ts, "$lte": ts + timedelta(days=7)}}):
        if _aware(ev["starts_at"]) - timedelta(minutes=ev.get("remind_minutes", 30)) <= ts:
            calendar_events_collection.update_one({"_id": ev["_id"]}, {"$set": {"reminded": True}})
            notify(ev["recruiter_id"], "Upcoming: " + ev["title"], f"Starts at {ev['starts_at']:%d %b %H:%M}.", "reminder", "/recruiter/calendar")
            n += 1
    return n


@router.post("/calendar/reminders/run")
def run_reminders_now(user=Depends(require_recruiter)):
    return {"sent": run_calendar_reminders()}


# ======================================================================= analytics
def _period(frm: Optional[datetime], to: Optional[datetime]) -> tuple[datetime, datetime]:
    end = _aware(to or datetime.now(timezone.utc))
    start = _aware(frm or (datetime.now(timezone.utc) - timedelta(days=30)))
    if start > end:
        raise HTTPException(422, "'from' must be before 'to'")
    return start, end


def job_analytics_rows(user, frm=None, to=None, job_id: Optional[str] = None) -> tuple[list[dict], dict]:
    start, end = _period(frm, to)
    q = {} if user["role"] == "admin" else {"recruiter_id": user["_id"]}
    if job_id:
        q["_id"] = oid(job_id, "job ID")
    jobs = list(jobs_collection.find(q))
    ids = [j["_id"] for j in jobs]
    views = {}
    sources: dict[str, int] = {}
    for v in job_views_collection.find({"job_id": {"$in": ids}, "at": {"$gte": start, "$lte": end}}):
        views[v["job_id"]] = views.get(v["job_id"], 0) + 1
        sources[v["source"]] = sources.get(v["source"], 0) + 1
    apps: dict = {}
    for a in applications_collection.find({"job_id": {"$in": ids}, "applied_at": {"$gte": start, "$lte": end}}):
        row = apps.setdefault(a["job_id"], {"applications": 0, "shortlisted": 0, "rejected": 0, "accepted": 0})
        row["applications"] += 1
        if a["status"] in row:
            row[a["status"]] += 1
    rows = []
    for j in jobs:
        a = apps.get(j["_id"], {"applications": 0, "shortlisted": 0, "rejected": 0, "accepted": 0})
        v = views.get(j["_id"], 0)
        rows.append({"job_id": str(j["_id"]), "title": j["title"], "views": v, **a, "conversion": round(a["applications"] / v * 100, 1) if v else 0.0})
    return rows, sources


@router.get("/analytics/jobs")
def job_analytics(
    frm: Optional[datetime] = Query(default=None, alias="from"),
    to: Optional[datetime] = None,
    job_id: Optional[str] = None,
    user=Depends(require_recruiter),
):
    rows, sources = job_analytics_rows(user, frm, to, job_id)
    tot = {k: sum(r[k] for r in rows) for k in ("views", "applications", "shortlisted", "rejected", "accepted")}
    tot["conversion"] = round(tot["applications"] / tot["views"] * 100, 1) if tot["views"] else 0.0
    return {"totals": tot, "jobs": rows, "sources": [{"source": k, "views": v} for k, v in sorted(sources.items(), key=lambda x: -x[1])]}


@router.get("/analytics/jobs/export")
def job_analytics_export(
    frm: Optional[datetime] = Query(default=None, alias="from"),
    to: Optional[datetime] = None,
    user=Depends(require_recruiter),
):
    rows, _ = job_analytics_rows(user, frm, to)
    return _csv(
        [[r["title"], r["views"], r["applications"], r["shortlisted"], r["rejected"], r["accepted"], r["conversion"]] for r in rows],
        ["Job", "Views", "Applications", "Shortlisted", "Rejected", "Accepted", "Conversion %"],
    )


# ======================================================================= team
ROLES = {
    "owner": ["jobs", "candidates", "interviews", "offers", "team", "analytics"],
    "hr_manager": ["jobs", "candidates", "interviews", "offers", "analytics"],
    "recruiter": ["jobs", "candidates", "interviews"],
    "interviewer": ["interviews", "evaluations"],
    "hiring_manager": ["candidates", "interviews", "evaluations", "offers", "analytics"],
}


class TeamInvite(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=120)
    role: Literal["hr_manager", "recruiter", "interviewer", "hiring_manager"]
    job_ids: list[str] = Field(default_factory=list, max_length=100)


class TeamUpdate(BaseModel):
    role: Optional[Literal["hr_manager", "recruiter", "interviewer", "hiring_manager"]] = None
    permissions: Optional[list[str]] = None
    is_active: Optional[bool] = None
    job_ids: Optional[list[str]] = Field(default=None, max_length=100)


def _need_company(user) -> dict:
    c = my_company(user)
    if not c:
        raise HTTPException(400, "Create your company profile first")
    return c


def _own_job_ids(user, ids: list[str]) -> list:
    out = []
    for i in dict.fromkeys(ids):
        out.append(_own_job(user, i)["_id"])
    return out


def _member_out(m: dict) -> dict:
    out = ser(m)
    out["permissions"] = m.get("permissions") or ROLES[m["role"]]
    out["job_ids"] = [str(j) for j in m.get("job_ids", [])]
    return out


@router.get("/team/roles")
def team_roles(user=Depends(require_recruiter)):
    return {"roles": {"owner": ROLES["owner"], **{k: v for k, v in ROLES.items() if k != "owner"}}, "permissions": sorted({p for v in ROLES.values() for p in v})}


@router.get("/team")
def team_list(user=Depends(require_recruiter)):
    c = _need_company(user)
    owner = {"id": str(user["_id"]), "name": user["name"], "email": user["email"], "role": "owner", "permissions": ROLES["owner"], "is_active": True, "status": "active", "job_ids": [], "is_owner": True}
    rows = [_member_out(m) for m in team_members_collection.find({"company_id": c["_id"]}).sort("created_at", 1)]
    return {"items": [owner] + rows, "total": len(rows) + 1}


@router.post("/team", status_code=201)
def team_invite(data: TeamInvite, user=Depends(require_recruiter)):
    c = _need_company(user)
    email = data.email.lower()
    if email == user["email"].lower():
        raise HTTPException(409, "You are already the company owner")
    if team_members_collection.find_one({"company_id": c["_id"], "email": email}):
        raise HTTPException(409, "This person is already on your team")
    ts = now()
    doc = {
        "company_id": c["_id"],
        "owner_id": user["_id"],
        "name": data.name.strip(),
        "email": email,
        "role": data.role,
        "permissions": None,
        "job_ids": _own_job_ids(user, data.job_ids),
        "is_active": True,
        "status": "invited",
        "invite_token": secrets.token_urlsafe(16),
        "activity": [{"at": ts, "action": "invited", "by": user["name"]}],
        "created_at": ts,
    }
    doc["_id"] = team_members_collection.insert_one(dict(doc)).inserted_id
    send_template("team_invite", email, {"name": data.name, "company": c["name"], "role": data.role.replace("_", " ")}, force=True)
    return _member_out(doc)


def _own_member(user, member_id: str) -> dict:
    c = _need_company(user)
    return get_or_404(team_members_collection, oid(member_id, "member ID"), "Team member", {"company_id": c["_id"]})


@router.patch("/team/{member_id}")
def team_update(member_id: str, data: TeamUpdate, user=Depends(require_recruiter)):
    m = _own_member(user, member_id)
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No data provided")
    if "permissions" in values and values["permissions"] is not None:
        allowed = {p for v in ROLES.values() for p in v}
        if set(values["permissions"]) - allowed:
            raise HTTPException(422, "Unknown permission")
    if "job_ids" in values and values["job_ids"] is not None:
        values["job_ids"] = _own_job_ids(user, values["job_ids"])
    actions = [f"{k} changed" for k in values]
    team_members_collection.update_one({"_id": m["_id"]}, {"$set": values, "$push": {"activity": {"$each": [{"at": now(), "action": a, "by": user["name"]} for a in actions]}}})
    return _member_out(team_members_collection.find_one({"_id": m["_id"]}))


@router.delete("/team/{member_id}")
def team_remove(member_id: str, user=Depends(require_recruiter)):
    m = _own_member(user, member_id)
    team_members_collection.delete_one({"_id": m["_id"]})
    return {"message": "Member removed"}


@router.get("/team/{member_id}/activity")
def team_activity(member_id: str, user=Depends(require_recruiter)):
    m = _own_member(user, member_id)
    items = [ser(a) for a in reversed(m.get("activity", []))]
    evs = [ser(e) for e in evaluations_collection.find({"recruiter_id": user["_id"], "reviewer": m["name"]}).sort("created_at", -1).limit(50)]
    return {"items": items, "evaluations": evs}


@router.get("/team/{member_id}/performance")
def team_performance(member_id: str, user=Depends(require_recruiter)):
    m = _own_member(user, member_id)
    jobs = m.get("job_ids", [])
    apps = list(applications_collection.find({"job_id": {"$in": jobs}}))
    by = lambda s: sum(1 for a in apps if a["status"] == s)
    evs = list(evaluations_collection.find({"recruiter_id": user["_id"], "reviewer": m["name"]}))
    return {
        "assigned_jobs": len(jobs),
        "applications": len(apps),
        "shortlisted": by("shortlisted"),
        "rejected": by("rejected"),
        "hired": by("accepted"),
        "evaluations_given": len(evs),
        "hire_rate": round(by("accepted") / len(apps) * 100, 1) if apps else 0.0,
    }


# ======================================================================= bulk candidates
EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STAGES = ["new", "screening", "interview", "offer", "hired", "rejected"]
MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 1000


@router.post("/candidates/import")
async def import_candidates(file: UploadFile = File(...), user=Depends(require_recruiter)):
    name = (file.filename or "").lower()
    if not name.endswith(".csv"):
        raise HTTPException(400, "Upload a .csv file")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "The file is empty")
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(400, "CSV is too large (max 1 MB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV must be UTF-8 encoded")
    reader = csv.DictReader(io.StringIO(text))
    cols = [(c or "").strip().lower() for c in (reader.fieldnames or [])]
    if "email" not in cols or "name" not in cols:
        raise HTTPException(422, "CSV needs 'name' and 'email' columns (optional: phone, skills, notes)")
    imported, errors, seen = 0, [], set()
    for n, row in enumerate(reader, start=2):
        if n - 1 > MAX_CSV_ROWS:
            errors.append({"row": n, "error": f"Row limit of {MAX_CSV_ROWS} reached - rest skipped"})
            break
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        email = r.get("email", "").lower()
        if len(r.get("name", "")) < 2:
            errors.append({"row": n, "error": "Name is required"})
        elif not EMAIL_RX.match(email):
            errors.append({"row": n, "error": f"Invalid email '{email[:60]}'"})
        elif email in seen:
            errors.append({"row": n, "error": "Duplicate email inside the file"})
        elif candidate_pool_collection.find_one({"recruiter_id": user["_id"], "email": email}):
            errors.append({"row": n, "error": "Already in your candidate pool"})
        else:
            seen.add(email)
            ts = now()
            candidate_pool_collection.insert_one(
                {
                    "recruiter_id": user["_id"],
                    "name": r["name"][:80],
                    "email": email,
                    "phone": r.get("phone", "")[:20],
                    "skills": [s.strip() for s in re.split(r"[;|,]", r.get("skills", "")) if s.strip()][:20],
                    "notes": r.get("notes", "")[:500],
                    "stage": "new",
                    "source": "csv",
                    "created_at": ts,
                    "updated_at": ts,
                }
            )
            imported += 1
    return {"imported": imported, "failed": len(errors), "errors": errors[:100]}


@router.get("/candidates/pool")
def pool_list(stage: Optional[str] = None, search: Optional[str] = Query(default=None, max_length=80), user=Depends(require_recruiter)):
    q: dict = {"recruiter_id": user["_id"]}
    if stage:
        if stage not in STAGES:
            raise HTTPException(422, "Unknown stage")
        q["stage"] = stage
    if search:
        q["$or"] = [{"name": {"$regex": re.escape(search), "$options": "i"}}, {"email": {"$regex": re.escape(search), "$options": "i"}}]
    rows = [ser(r) for r in candidate_pool_collection.find(q).sort("created_at", -1).limit(500)]
    return {"items": rows, "total": len(rows), "stages": STAGES}


class BulkStage(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    stage: Literal["new", "screening", "interview", "offer", "hired", "rejected"]


@router.post("/candidates/pool/stage")
def pool_stage(data: BulkStage, user=Depends(require_recruiter)):
    ids = [oid(i, "candidate ID") for i in data.ids]
    res = candidate_pool_collection.update_many({"_id": {"$in": ids}, "recruiter_id": user["_id"]}, {"$set": {"stage": data.stage, "updated_at": now()}})
    return {"updated": res.modified_count}


@router.post("/candidates/pool/delete")
def pool_delete(data: dict, user=Depends(require_recruiter)):
    ids = [oid(i, "candidate ID") for i in (data.get("ids") or [])[:200]]
    res = candidate_pool_collection.delete_many({"_id": {"$in": ids}, "recruiter_id": user["_id"]})
    return {"deleted": res.deleted_count}


@router.get("/candidates/pool/export")
def pool_export(user=Depends(require_recruiter)):
    rows = candidate_pool_collection.find({"recruiter_id": user["_id"]}).sort("created_at", -1)
    return _csv([[r["name"], r["email"], r.get("phone", ""), ";".join(r.get("skills", [])), r["stage"], r.get("notes", "")] for r in rows], ["Name", "Email", "Phone", "Skills", "Stage", "Notes"])


class BulkAppStatus(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    status: Optional[Literal["applied", "shortlisted", "rejected", "accepted"]] = None
    stage: Optional[Literal["new", "screening", "interview", "offer", "hired", "rejected"]] = None
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/applications/bulk")
def bulk_application_status(data: BulkAppStatus, user=Depends(require_recruiter)):
    if not data.status and not data.stage:
        raise HTTPException(422, "Choose a status or a stage")
    ts, done = now(), 0
    for i in data.ids:
        app = applications_collection.find_one({"_id": oid(i, "application ID"), **_app_scope(user)})
        if not app or app["status"] == "withdrawn":
            continue
        sets, push = {"updated_at": ts}, None
        if data.stage:
            sets["stage"] = data.stage
        if data.status and data.status != app["status"]:
            sets["status"] = data.status
            push = {"status_history": {"status": data.status, "at": ts, "note": data.note or "Bulk update"}}
            notify(app["user_id"], "Application update", f"Your application for {app.get('job_title')} is now '{data.status}'.", "application", "/applications")
        applications_collection.update_one({"_id": app["_id"]}, {"$set": sets, **({"$push": push} if push else {})})
        done += 1
    return {"updated": done, "skipped": len(data.ids) - done}


@router.get("/applications/export")
def applications_export(job_id: Optional[str] = None, status: Optional[str] = None, user=Depends(require_recruiter)):
    q = _app_scope(user)
    if job_id:
        q["job_id"] = oid(job_id, "job ID")
    if status:
        q["status"] = status
    apps = list(applications_collection.find(q).sort("applied_at", -1).limit(5000))
    users = {u["_id"]: u for u in users_collection.find({"_id": {"$in": [a["user_id"] for a in apps]}})}
    return _csv(
        [[users[a["user_id"]]["name"] if a["user_id"] in users else "", users[a["user_id"]]["email"] if a["user_id"] in users else "", a.get("job_title", ""), a["status"], a.get("stage", ""), a["applied_at"].strftime("%Y-%m-%d")] for a in apps],
        ["Name", "Email", "Job", "Status", "Stage", "Applied"],
    )


class BulkMessage(BaseModel):
    application_ids: list[str] = Field(default_factory=list, max_length=200)
    pool_ids: list[str] = Field(default_factory=list, max_length=200)
    subject: str = Field(min_length=2, max_length=120)
    message: str = Field(min_length=2, max_length=2000)


@router.post("/candidates/message")
def bulk_message(data: BulkMessage, user=Depends(require_recruiter)):
    if not data.application_ids and not data.pool_ids:
        raise HTTPException(422, "Select at least one candidate")
    sent_in_app = sent_email = 0
    company = _company_name(user)
    for i in data.application_ids:
        app = applications_collection.find_one({"_id": oid(i, "application ID"), **_app_scope(user)})
        if app:
            notify(app["user_id"], data.subject, data.message, "message", "/applications")
            sent_in_app += 1
    for i in data.pool_ids:
        p = candidate_pool_collection.find_one({"_id": oid(i, "candidate ID"), "recruiter_id": user["_id"]})
        if p:
            send_template("recruiter_message", p["email"], {"name": p["name"], "company": company, "subject": data.subject, "message": data.message}, force=True)
            sent_email += 1
    return {"in_app": sent_in_app, "email": sent_email}


# ======================================================================= offer letters
class OfferTemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    body: str = Field(min_length=20, max_length=6000)
    terms: Optional[str] = Field(default=None, max_length=3000)


class OfferIn(BaseModel):
    application_id: str
    designation: str = Field(min_length=2, max_length=100)
    salary: float = Field(gt=0, le=1_000_000_000)
    currency: Literal["INR", "USD", "EUR", "GBP"] = "INR"
    joining_date: Optional[datetime] = None
    expires_in_days: int = Field(default=7, ge=1, le=60)
    template_id: Optional[str] = None
    terms: Optional[str] = Field(default=None, max_length=3000)
    template_style: Literal["modern_corporate", "minimal_professional", "premium_corporate", "classic_formal", "modern_gradient", "vibrant_swoosh", "playful_circles"] = "modern_corporate"
    probation_months: Optional[int] = Field(default=None, ge=0, le=24)
    working_hours: Optional[str] = Field(default=None, max_length=120)
    notice_period: Optional[str] = Field(default=None, max_length=60)
    company_address: Optional[str] = Field(default=None, max_length=200)
    company_website: HttpUrl = None
    company_email: Optional[str] = Field(default=None, max_length=120)
    company_phone: Optional[str] = Field(default=None, max_length=40)
    company_logo_url: MediaUrl = None
    signatory_name: Optional[str] = Field(default=None, max_length=100)
    signatory_title: Optional[str] = Field(default=None, max_length=100)
    signature_asset_id: Optional[str] = None


class OfferUpdate(BaseModel):
    designation: Optional[str] = Field(default=None, min_length=2, max_length=100)
    salary: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)
    joining_date: Optional[datetime] = None
    terms: Optional[str] = Field(default=None, max_length=3000)
    body: Optional[str] = Field(default=None, min_length=20, max_length=6000)
    template_style: Optional[Literal["modern_corporate", "minimal_professional", "premium_corporate", "classic_formal", "modern_gradient", "vibrant_swoosh", "playful_circles"]] = None
    probation_months: Optional[int] = Field(default=None, ge=0, le=24)
    working_hours: Optional[str] = Field(default=None, max_length=120)
    notice_period: Optional[str] = Field(default=None, max_length=60)
    company_address: Optional[str] = Field(default=None, max_length=200)
    company_website: HttpUrl = None
    company_email: Optional[str] = Field(default=None, max_length=120)
    company_phone: Optional[str] = Field(default=None, max_length=40)
    company_logo_url: MediaUrl = None
    signatory_name: Optional[str] = Field(default=None, max_length=100)
    signatory_title: Optional[str] = Field(default=None, max_length=100)
    signature_asset_id: Optional[str] = None


def _tpl_scope(user) -> dict:
    return {"recruiter_id": user["_id"]}


@router.get("/offer-templates")
def tpl_list(user=Depends(require_recruiter)):
    rows = [ser(t) for t in offer_templates_collection.find(_tpl_scope(user)).sort("created_at", -1)]
    return {"items": rows, "default": {"body": DEFAULT_BODY, "terms": DEFAULT_TERMS}, "placeholders": ["candidate_name", "designation", "company", "salary", "joining_date", "terms", "recruiter_name", "expires", "date"]}


@router.post("/offer-templates", status_code=201)
def tpl_create(data: OfferTemplateIn, user=Depends(require_recruiter)):
    doc = {**data.model_dump(), "recruiter_id": user["_id"], "created_at": now()}
    doc["_id"] = offer_templates_collection.insert_one(dict(doc)).inserted_id
    return ser(doc)


@router.put("/offer-templates/{tpl_id}")
def tpl_update(tpl_id: str, data: OfferTemplateIn, user=Depends(require_recruiter)):
    t = get_or_404(offer_templates_collection, oid(tpl_id, "template ID"), "Template", _tpl_scope(user))
    offer_templates_collection.update_one({"_id": t["_id"]}, {"$set": data.model_dump()})
    return ser(offer_templates_collection.find_one({"_id": t["_id"]}))


@router.delete("/offer-templates/{tpl_id}")
def tpl_delete(tpl_id: str, user=Depends(require_recruiter)):
    t = get_or_404(offer_templates_collection, oid(tpl_id, "template ID"), "Template", _tpl_scope(user))
    offer_templates_collection.delete_one({"_id": t["_id"]})
    return {"message": "Template deleted"}


def _own_offer(user, offer_id: str) -> dict:
    return get_or_404(offers_collection, oid(offer_id, "offer ID"), "Offer", {} if user["role"] == "admin" else {"recruiter_id": user["_id"]})


@router.get("/offers")
def offers_list(status: Optional[str] = None, user=Depends(require_recruiter)):
    q = {} if user["role"] == "admin" else {"recruiter_id": user["_id"]}
    if status:
        q["status"] = status
    rows = list(offers_collection.find(q).sort("created_at", -1).limit(200))
    counts: dict[str, int] = {}
    for r in offers_collection.find({} if user["role"] == "admin" else {"recruiter_id": user["_id"]}, {"status": 1}):
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"items": [offer_out(r) for r in rows], "total": len(rows), "counts": counts}


def _branding(user, data) -> dict:
    """Style, letterhead and signature fields; blanks are filled from the recruiter's company profile."""
    co = my_company(user) or {}
    v = data.model_dump(exclude_unset=True)
    sig = v.get("signature_asset_id")
    if sig:
        try:
            asset = offer_assets_collection.find_one({"_id": oid(sig, "signature"), **({} if user["role"] == "admin" else {"recruiter_id": user["_id"]})})
        except HTTPException:
            asset = None
        if not asset:
            raise HTTPException(422, "Signature image not found. Upload it again.")
        v["signature_asset_id"] = asset["_id"]
    out = {k: v[k] for k in ("template_style", "probation_months", "working_hours", "notice_period", "company_address", "company_website", "company_email", "company_phone", "company_logo_url", "signatory_name", "signatory_title", "signature_asset_id") if k in v}
    if "company_logo_url" not in v and co.get("logo_url"):
        out["company_logo_url"] = co["logo_url"]
    if "company_website" not in v and co.get("website"):
        out["company_website"] = co["website"]
    if "company_address" not in v and co.get("location"):
        out["company_address"] = co["location"]
    out.setdefault("template_style", "modern_corporate")
    out.setdefault("signatory_name", user["name"])
    return out


@router.get("/offer-styles")
def offer_styles(user=Depends(require_recruiter)):
    return {"items": styles_out()}


SIGNATURE_MAX_BYTES = 400 * 1024
SIGNATURE_MAGIC = {".png": b"\x89PNG", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}


@router.post("/offer-assets/signature", status_code=201)
async def upload_signature(file: UploadFile = File(...), user=Depends(require_recruiter)):
    """Digital signature image for offer letters. Stored privately; only the PDF builder reads it."""
    import os

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in SIGNATURE_MAGIC:
        raise HTTPException(400, "Signature must be a PNG or JPG image")
    content = await file.read(SIGNATURE_MAX_BYTES + 1)
    if len(content) > SIGNATURE_MAX_BYTES:
        raise HTTPException(400, "Signature image is too large (max 400 KB)")
    if not content.startswith(SIGNATURE_MAGIC[ext]):
        raise HTTPException(400, "The file is not a valid PNG/JPG image")
    saved = storage.store_private(content, ext, "signatures")
    doc = {"recruiter_id": user["_id"], "kind": "signature", "storage": saved["provider"], "storage_ref": saved["ref"], "size": len(content), "created_at": now()}
    doc["_id"] = offer_assets_collection.insert_one(dict(doc)).inserted_id
    import base64

    mime = "image/png" if ext == ".png" else "image/jpeg"
    return {"id": str(doc["_id"]), "data_url": f"data:{mime};base64,{base64.b64encode(content).decode()}"}


@router.post("/offers", status_code=201)
def offer_create(data: OfferIn, user=Depends(require_recruiter)):
    app = _scope_app(user, data.application_id)
    if app["status"] in ("withdrawn", "rejected"):
        raise HTTPException(400, f"Cannot make an offer on a {app['status']} application")
    if offers_collection.find_one({"application_id": app["_id"], "status": {"$in": ["draft", "sent", "accepted"]}}):
        raise HTTPException(409, "An active offer already exists for this application")
    cand = users_collection.find_one({"_id": app["user_id"]})
    body, terms = None, data.terms
    if data.template_id:
        t = get_or_404(offer_templates_collection, oid(data.template_id, "template ID"), "Template", _tpl_scope(user))
        body, terms = t["body"], terms or t.get("terms")
    ts = now()
    doc = {
        "application_id": app["_id"],
        "job_id": app["job_id"],
        "company_id": app.get("company_id"),
        "recruiter_id": user["_id"],
        "candidate_id": app["user_id"],
        "candidate_name": cand["name"] if cand else "Candidate",
        "designation": data.designation.strip(),
        "salary": data.salary,
        "currency": data.currency,
        "joining_date": data.joining_date,
        "terms": terms,
        "body": body,
        "company_name": app.get("company_name") or _company_name(user),
        "recruiter_name": user["name"],
        "expires_days": data.expires_in_days,
        **_branding(user, data),
        "status": "draft",
        "history": [{"status": "draft", "at": ts}],
        "created_at": ts,
        "updated_at": ts,
    }
    doc["_id"] = offers_collection.insert_one(dict(doc)).inserted_id
    return offer_out(doc, with_letter=True)


@router.get("/offers/{offer_id}")
def offer_get(offer_id: str, user=Depends(require_recruiter)):
    return offer_out(_own_offer(user, offer_id), with_letter=True)


@router.put("/offers/{offer_id}")
def offer_update(offer_id: str, data: OfferUpdate, user=Depends(require_recruiter)):
    o = _own_offer(user, offer_id)
    if o["status"] != "draft":
        raise HTTPException(400, "Only draft offers can be edited")
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No data provided")
    if "signature_asset_id" in values:
        values["signature_asset_id"] = _branding(user, data)["signature_asset_id"] if values["signature_asset_id"] else None
    values["updated_at"] = now()
    offers_collection.update_one({"_id": o["_id"]}, {"$set": values})
    return offer_out(offers_collection.find_one({"_id": o["_id"]}), with_letter=True)


@router.post("/offers/{offer_id}/send")
def offer_send(offer_id: str, user=Depends(require_recruiter)):
    o = _own_offer(user, offer_id)
    if o["status"] != "draft":
        raise HTTPException(400, f"This offer is already {o['status']}")
    ts = now()
    exp = ts + timedelta(days=o.get("expires_days", 7))
    offers_collection.update_one(
        {"_id": o["_id"], "status": "draft"},
        {"$set": {"status": "sent", "sent_at": ts, "expires_at": exp, "updated_at": ts}, "$push": {"history": {"status": "sent", "at": ts}}},
    )
    o = offers_collection.find_one({"_id": o["_id"]})
    notify(o["candidate_id"], "You have a new job offer", f"{o['company_name']} sent you an offer for {o['designation']}.", "application", "/offers")
    cand = users_collection.find_one({"_id": o["candidate_id"]})
    if cand:
        attach = []
        try:  # the signed letter travels with the e-mail so the candidate has a copy even before logging in
            attach.append((f"Offer-{o['company_name'][:30].replace(' ', '_')}.pdf", render_pdf(o), "application/pdf"))
        except Exception:  # pragma: no cover - the e-mail must go out even if the PDF could not be built
            pass
        send_template("offer_letter", cand["email"], {"name": cand["name"], "company": o["company_name"], "designation": o["designation"], "link": f"{PUBLIC_APP_URL}/offers"}, user_id=cand["_id"], attachments=attach)
    applications_collection.update_one({"_id": o["application_id"]}, {"$set": {"stage": "offer", "updated_at": ts}})
    return offer_out(o, with_letter=True)


@router.post("/offers/{offer_id}/withdraw")
def offer_withdraw(offer_id: str, user=Depends(require_recruiter)):
    o = _own_offer(user, offer_id)
    if o["status"] not in ("draft", "sent"):
        raise HTTPException(400, f"A {o['status']} offer cannot be withdrawn")
    ts = now()
    offers_collection.update_one({"_id": o["_id"]}, {"$set": {"status": "withdrawn", "updated_at": ts}, "$push": {"history": {"status": "withdrawn", "at": ts}}})
    if o["status"] == "sent":
        notify(o["candidate_id"], "Offer withdrawn", f"{o['company_name']} withdrew the offer for {o['designation']}.", "application", "/offers")
    return offer_out(offers_collection.find_one({"_id": o["_id"]}))


@router.get("/offers/{offer_id}/pdf")
def offer_pdf(offer_id: str, user=Depends(require_recruiter)):
    o = _own_offer(user, offer_id)
    return Response(render_pdf(o), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=offer-{str(o['_id'])[-6:]}.pdf"})


# ======================================================================= files uploaded as screening answers
ANSWER_LINK_SECONDS = 300


def _answer_sig(file_id: str, exp: int) -> str:
    import hashlib
    import hmac

    from app.config import JWT_SECRET

    return hmac.new(JWT_SECRET.encode(), f"answer:{file_id}:{exp}".encode(), hashlib.sha256).hexdigest()


@router.get("/answer-files/{file_id}/link")
def answer_file_link(file_id: str, user=Depends(require_recruiter)):
    """Short-lived signed link. Only the recruiter who owns the job (or an admin) can ask for it."""
    import time

    from app.database import answer_files_collection

    f = get_or_404(answer_files_collection, oid(file_id, "file ID"), "File")
    job = jobs_collection.find_one({"_id": f["job_id"]}) or {}
    if user["role"] != "admin" and job.get("recruiter_id") != user["_id"]:
        raise HTTPException(404, "File not found")
    exp = int(time.time()) + ANSWER_LINK_SECONDS
    return {"url": f"/api/files/answer/{file_id}?exp={exp}&sig={_answer_sig(file_id, exp)}", "expires_in": ANSWER_LINK_SECONDS}


@files_router.get("/answer/{file_id}")
def answer_file(file_id: str, exp: int = Query(...), sig: str = Query(..., max_length=128)):
    import hmac
    import os
    import time

    from fastapi.responses import Response

    from app.database import answer_files_collection
    from app.services import storage

    if exp < time.time() or not hmac.compare_digest(sig, _answer_sig(file_id, exp)):
        raise HTTPException(403, "This download link is invalid or has expired")
    f = get_or_404(answer_files_collection, oid(file_id, "file ID"), "File")
    data = storage.read_private(f.get("storage"), f.get("storage_ref"))
    name = "".join(c for c in f["name"] if c.isalnum() or c in " ._-")[:100] or "file"
    mime = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
