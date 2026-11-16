"""Candidate career tools: job alerts + preferences, document vault, referrals, career events, offers, followed companies."""
import hashlib
import hmac
import os
import secrets
import time
import uuid
from datetime import timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.config import JWT_SECRET, PUBLIC_APP_URL, UPLOAD_DIR
from app.database import (
    applications_collection,
    companies_collection,
    documents_collection,
    event_registrations_collection,
    events_collection,
    followers_collection,
    jobs_collection,
    offers_collection,
    referral_invites_collection,
    referrals_collection,
    users_collection,
)
from app.dependencies.auth import get_current_user, require_roles
from app.schemas.common import Email
from app.services import simplepdf
from app.services.email import send_template
from app.services.jobs import public_visibility, shape_jobs
from app.services.notify import notify
from app.services.offers import letter_paragraphs, offer_out
from app.utils import get_or_404, now, oid, paginate, ser

router = APIRouter(dependencies=[Depends(require_roles("user"))])
files_router = APIRouter()
DOC_DIR = UPLOAD_DIR / "documents"
DOC_DIR.mkdir(parents=True, exist_ok=True)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


# =========================================================== job alerts & preferences
JOB_TYPES = ["fulltime", "parttime", "internship", "contract", "freelance"]
WORK_MODES = ["remote", "hybrid", "onsite"]


class PreferencesIn(BaseModel):
    titles: list[str] = Field(default_factory=list, max_length=10)
    industries: list[str] = Field(default_factory=list, max_length=10)
    locations: list[str] = Field(default_factory=list, max_length=10)
    salary_min: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    salary_max: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    job_types: list[Literal["fulltime", "parttime", "internship", "contract", "freelance"]] = Field(default_factory=list)
    work_modes: list[Literal["remote", "hybrid", "onsite"]] = Field(default_factory=list)
    alerts_enabled: bool = False
    frequency: Literal["daily", "weekly"] = "daily"


def _clean(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        s = i.strip()[:60]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _prefs(user: dict) -> dict:
    return {"titles": [], "industries": [], "locations": [], "salary_min": None, "salary_max": None, "job_types": [], "work_modes": [], "alerts_enabled": False, "frequency": "daily", **(user.get("job_preferences") or {})}


def job_matches(job: dict, company: dict, p: dict) -> bool:
    """Every preference the candidate filled in has to fit; empty preferences are ignored."""
    low = lambda s: (s or "").lower()
    if p["titles"] and not any(t.lower() in low(job.get("title")) or t.lower() in " ".join(job.get("skills", [])).lower() for t in p["titles"]):
        return False
    if p["industries"] and not any(i.lower() in low((company or {}).get("industry")) for i in p["industries"]):
        return False
    if p["locations"] and not any(l.lower() in low(job.get("location")) or (l.lower() == "remote" and job.get("work_mode") == "remote") for l in p["locations"]):
        return False
    if p["job_types"] and job.get("job_type") not in p["job_types"]:
        return False
    if p["work_modes"] and job.get("work_mode") not in p["work_modes"]:
        return False
    if p["salary_min"] is not None and job.get("salary_max") is not None and job["salary_max"] < p["salary_min"]:
        return False
    if p["salary_max"] is not None and job.get("salary_min") is not None and job["salary_min"] > p["salary_max"]:
        return False
    return True


def find_matches(p: dict, since=None, limit: int = 20) -> list[dict]:
    q = dict(public_visibility())
    if since:
        q["created_at"] = {"$gte": since}
    out = []
    companies: dict = {}
    for j in jobs_collection.find(q).sort("created_at", -1).limit(400):
        c = companies.get(j.get("company_id"))
        if c is None:
            c = companies[j.get("company_id")] = companies_collection.find_one({"_id": j.get("company_id")}) or {}
        if job_matches(j, c, p):
            out.append(j)
            if len(out) >= limit:
                break
    return out


@router.get("/me/preferences")
def get_preferences(user=Depends(get_current_user)):
    return {**_prefs(user), "options": {"job_types": JOB_TYPES, "work_modes": WORK_MODES}}


@router.put("/me/preferences")
def put_preferences(data: PreferencesIn, user=Depends(get_current_user)):
    if data.salary_min is not None and data.salary_max is not None and data.salary_max < data.salary_min:
        raise HTTPException(422, "Maximum salary must be at least the minimum")
    doc = data.model_dump()
    for k in ("titles", "industries", "locations"):
        doc[k] = _clean(doc[k])
    if data.alerts_enabled and not any(doc[k] for k in ("titles", "industries", "locations", "job_types", "work_modes")):
        raise HTTPException(422, "Choose at least one title, industry, location, job type or work style to get alerts")
    doc["alerts_updated_at"] = now()
    old = user.get("job_preferences") or {}
    doc["last_alert_at"] = old.get("last_alert_at") or now()
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"job_preferences": doc, "updated_at": now()}})
    return _prefs({"job_preferences": doc})


@router.get("/me/job-matches")
def job_matches_feed(days: int = Query(30, ge=1, le=90), limit: int = Query(20, ge=1, le=50), user=Depends(get_current_user)):
    p = _prefs(user)
    has = any(p[k] for k in ("titles", "industries", "locations", "job_types", "work_modes")) or p["salary_min"] is not None or p["salary_max"] is not None
    if not has:
        return {"items": [], "total": 0, "configured": False}
    rows = find_matches(p, now() - timedelta(days=days), limit)
    return {"items": shape_jobs(rows, user), "total": len(rows), "configured": True}


def run_alert_digests(at=None) -> int:
    """Create one in-app notification (and e-mail) per candidate with new matches since their last digest. Called by the scheduler."""
    at = at or now()
    sent = 0
    for u in users_collection.find({"role": "user", "is_active": True, "job_preferences.alerts_enabled": True}):
        p = _prefs(u)
        last = _aware((u.get("job_preferences") or {}).get("last_alert_at")) or at - timedelta(days=1)
        gap = timedelta(days=7 if p["frequency"] == "weekly" else 1)
        if at - last < gap:
            continue
        users_collection.update_one({"_id": u["_id"]}, {"$set": {"job_preferences.last_alert_at": at}})
        rows = find_matches(p, last, 10)
        if not rows:
            continue
        titles = ", ".join(r["title"] for r in rows[:3])
        notify(u["_id"], f"{len(rows)} new job{'s' if len(rows) > 1 else ''} match your alerts", titles, "job", "/jobs")
        send_template("job_alert", u["email"], {"name": u["name"], "details": titles, "status": str(len(rows))}, u["_id"])
        sent += 1
    return sent


# =========================================================== document vault
DOC_CATEGORIES = ["resume", "certificate", "education", "experience", "other"]
DOC_EXT = {".pdf": b"%PDF", ".docx": b"PK", ".doc": b"\xd0\xcf\x11\xe0", ".png": b"\x89PNG", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}
MAX_DOC_MB = 8
MAX_DOCS = 40
LINK_SECONDS = 300


def _doc_out(d: dict) -> dict:
    out = ser({k: v for k, v in d.items() if k != "stored_name"})
    out["ext"] = os.path.splitext(d.get("filename", ""))[1].lower().strip(".")
    return out


async def _read_validated(file: UploadFile) -> tuple[bytes, str, str]:
    original = os.path.basename(file.filename or "document")
    ext = os.path.splitext(original)[1].lower()
    if ext not in DOC_EXT:
        raise HTTPException(400, "Allowed files: PDF, DOC, DOCX, PNG, JPG")
    content = await file.read()
    if not content:
        raise HTTPException(400, "The file is empty")
    if len(content) > MAX_DOC_MB * 1024 * 1024:
        raise HTTPException(400, f"File is too large (max {MAX_DOC_MB} MB)")
    if not content.startswith(DOC_EXT[ext]):
        raise HTTPException(400, f"The file content does not look like a valid {ext.strip('.').upper()} file")
    return content, original[:150], ext


@router.get("/me/documents")
def list_documents(category: Optional[str] = Query(default=None, pattern="^(resume|certificate|education|experience|other)$"), user=Depends(get_current_user)):
    q: dict = {"user_id": user["_id"]}
    if category:
        q["category"] = category
    rows = list(documents_collection.find(q).sort("created_at", -1))
    counts = {c: documents_collection.count_documents({"user_id": user["_id"], "category": c}) for c in DOC_CATEGORIES}
    return {"items": [_doc_out(r) for r in rows], "counts": counts, "categories": DOC_CATEGORIES, "max_mb": MAX_DOC_MB}


@router.post("/me/documents", status_code=201)
async def upload_document(file: UploadFile = File(...), category: str = Form("other"), title: str = Form(""), user=Depends(get_current_user)):
    if category not in DOC_CATEGORIES:
        raise HTTPException(422, "Unknown category")
    if documents_collection.count_documents({"user_id": user["_id"]}) >= MAX_DOCS:
        raise HTTPException(400, f"You can store up to {MAX_DOCS} documents. Delete some first.")
    content, original, ext = await _read_validated(file)
    stored = f"{uuid.uuid4()}{ext}"
    (DOC_DIR / stored).write_bytes(content)
    ts = now()
    doc = {"user_id": user["_id"], "title": (title.strip() or os.path.splitext(original)[0])[:120], "category": category, "filename": original, "stored_name": stored, "size": len(content), "created_at": ts, "updated_at": ts}
    doc["_id"] = documents_collection.insert_one(dict(doc)).inserted_id
    return _doc_out(doc)


@router.put("/me/documents/{doc_id}/file")
async def replace_document(doc_id: str, file: UploadFile = File(...), user=Depends(get_current_user)):
    d = get_or_404(documents_collection, oid(doc_id, "document ID"), "Document", {"user_id": user["_id"]})
    content, original, ext = await _read_validated(file)
    stored = f"{uuid.uuid4()}{ext}"
    (DOC_DIR / stored).write_bytes(content)
    old = DOC_DIR / os.path.basename(d.get("stored_name", ""))
    documents_collection.update_one({"_id": d["_id"]}, {"$set": {"filename": original, "stored_name": stored, "size": len(content), "updated_at": now()}})
    if old.is_file():
        old.unlink()
    return _doc_out(documents_collection.find_one({"_id": d["_id"]}))


class DocUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[Literal["resume", "certificate", "education", "experience", "other"]] = None


@router.patch("/me/documents/{doc_id}")
def update_document(doc_id: str, data: DocUpdate, user=Depends(get_current_user)):
    d = get_or_404(documents_collection, oid(doc_id, "document ID"), "Document", {"user_id": user["_id"]})
    v = data.model_dump(exclude_unset=True)
    if not v:
        raise HTTPException(400, "No data provided")
    v["updated_at"] = now()
    documents_collection.update_one({"_id": d["_id"]}, {"$set": v})
    return _doc_out(documents_collection.find_one({"_id": d["_id"]}))


@router.delete("/me/documents/{doc_id}")
def delete_document(doc_id: str, user=Depends(get_current_user)):
    d = get_or_404(documents_collection, oid(doc_id, "document ID"), "Document", {"user_id": user["_id"]})
    documents_collection.delete_one({"_id": d["_id"]})
    p = DOC_DIR / os.path.basename(d.get("stored_name", ""))
    if p.is_file():
        p.unlink()
    return {"message": "Document deleted"}


def _doc_sig(doc_id: str, exp: int, download: int) -> str:
    return hmac.new(JWT_SECRET.encode(), f"doc:{doc_id}:{exp}:{download}".encode(), hashlib.sha256).hexdigest()


@router.get("/me/documents/{doc_id}/link")
def document_link(doc_id: str, download: bool = False, user=Depends(get_current_user)):
    d = get_or_404(documents_collection, oid(doc_id, "document ID"), "Document", {"user_id": user["_id"]})
    exp = int(time.time()) + LINK_SECONDS
    dl = 1 if download else 0
    return {"url": f"/api/files/doc/{d['_id']}?exp={exp}&dl={dl}&sig={_doc_sig(str(d['_id']), exp, dl)}", "expires_in": LINK_SECONDS}


MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


@files_router.get("/doc/{doc_id}")
def document_file(doc_id: str, exp: int = Query(...), dl: int = Query(0), sig: str = Query(..., max_length=128)):
    if exp < time.time() or not hmac.compare_digest(sig, _doc_sig(doc_id, exp, dl)):
        raise HTTPException(403, "This link is invalid or has expired")
    d = documents_collection.find_one({"_id": oid(doc_id, "document ID")})
    path = DOC_DIR / os.path.basename((d or {}).get("stored_name", "x"))
    if not d or not path.is_file():
        raise HTTPException(404, "File not found")
    ext = path.suffix.lower()
    safe = "".join(c for c in d["filename"] if c.isalnum() or c in " ._-")[:100] or "document"
    return FileResponse(path, media_type=MIME.get(ext, "application/octet-stream"), headers={"Content-Disposition": f"{'attachment' if dl else 'inline'}; filename=\"{safe}\"", "X-Content-Type-Options": "nosniff"})


# =========================================================== referrals & rewards
POINTS_PER_REFERRAL = 50
MILESTONES = [(1, "Starter badge", None), (3, "Connector badge", None), (5, "7 days of Premium", 7), (10, "14 days of Premium", 14)]


def ensure_code(user: dict) -> str:
    if user.get("referral_code"):
        return user["referral_code"]
    for _ in range(10):
        code = secrets.token_hex(4).upper()
        if not users_collection.find_one({"referral_code": code}):
            users_collection.update_one({"_id": user["_id"]}, {"$set": {"referral_code": code}})
            return code
    raise HTTPException(500, "Could not create a referral code")


def register_referral(new_user: dict, code: Optional[str]) -> None:
    """Called at sign-up. Silently ignores unknown codes."""
    if not code:
        return
    ref = users_collection.find_one({"referral_code": code.strip().upper(), "is_active": True})
    if not ref or ref["_id"] == new_user["_id"] or ref["role"] != "user":
        return
    try:
        referrals_collection.insert_one({"referrer_id": ref["_id"], "referred_id": new_user["_id"], "points": POINTS_PER_REFERRAL, "created_at": now()})
    except Exception:
        return
    notify(ref["_id"], "Your referral joined CareVo", f"{new_user['name']} signed up with your code. +{POINTS_PER_REFERRAL} points!", "system", "/referrals")
    _grant_milestones(ref)


def _grant_milestones(ref: dict) -> None:
    count = referrals_collection.count_documents({"referrer_id": ref["_id"]})
    claimed = set(ref.get("referral_rewards") or [])
    for need, label, days in MILESTONES:
        if count >= need and need not in claimed:
            claimed.add(need)
            if days:
                base = _aware(ref.get("premium_until")) if ref.get("is_premium") else None
                start = base if base and base > now() else now()
                users_collection.update_one({"_id": ref["_id"]}, {"$set": {"is_premium": True, "premium_until": start + timedelta(days=days)}})
            users_collection.update_one({"_id": ref["_id"]}, {"$set": {"referral_rewards": sorted(claimed)}})
            notify(ref["_id"], f"Reward unlocked: {label}", f"You reached {need} referral{'s' if need > 1 else ''}.", "system", "/referrals")


@router.get("/me/referrals")
def my_referrals(user=Depends(get_current_user)):
    code = ensure_code(user)
    user = users_collection.find_one({"_id": user["_id"]})
    rows = list(referrals_collection.find({"referrer_id": user["_id"]}).sort("created_at", -1))
    names = {u["_id"]: u["name"] for u in users_collection.find({"_id": {"$in": [r["referred_id"] for r in rows]}}, {"name": 1})}
    invites = list(referral_invites_collection.find({"referrer_id": user["_id"]}).sort("created_at", -1).limit(50))
    joined_emails = {u["email"] for u in users_collection.find({"_id": {"$in": [r["referred_id"] for r in rows]}}, {"email": 1})}
    claimed = set(user.get("referral_rewards") or [])
    return {
        "code": code, "link": f"{PUBLIC_APP_URL}/register?ref={code}", "total": len(rows), "points": sum(r["points"] for r in rows), "invited": len(invites),
        "history": [{"name": names.get(r["referred_id"], "Member"), "points": r["points"], "joined_at": ser(r["created_at"])} for r in rows],
        "invites": [{"email": i["email"], "sent_at": ser(i["created_at"]), "joined": i["email"] in joined_emails} for i in invites],
        "rewards": [{"need": n, "label": label, "unlocked": n in claimed, "remaining": max(0, n - len(rows))} for n, label, _ in MILESTONES],
        "report": {"conversion_rate": round(100 * len(rows) / len(invites), 1) if invites else None, "last_30_days": sum(1 for r in rows if _aware(r["created_at"]) > now() - timedelta(days=30))},
    }


class InviteIn(BaseModel):
    emails: list[Email] = Field(min_length=1, max_length=10)


@router.post("/me/referrals/invite", status_code=201)
def invite_friends(data: InviteIn, user=Depends(get_current_user)):
    code = ensure_code(user)
    if referral_invites_collection.count_documents({"referrer_id": user["_id"], "created_at": {"$gte": now() - timedelta(days=1)}}) + len(data.emails) > 20:
        raise HTTPException(429, "You can invite up to 20 friends a day")
    sent, skipped = 0, []
    for e in dict.fromkeys(data.emails):
        if e == user["email"] or users_collection.find_one({"email": e}) or referral_invites_collection.find_one({"referrer_id": user["_id"], "email": e}):
            skipped.append(e)
            continue
        referral_invites_collection.insert_one({"referrer_id": user["_id"], "email": e, "created_at": now()})
        send_template("referral_invite", e, {"name": user["name"], "link": f"{PUBLIC_APP_URL}/register?ref={code}"}, force=True)
        sent += 1
    return {"message": f"{sent} invite(s) sent", "sent": sent, "skipped": skipped}


# =========================================================== career events & webinars
def _event_out(e: dict, uid, registered_ids: set, with_link: bool = True) -> dict:
    out = ser(e)
    reg = e["_id"] in registered_ids
    out["registered"] = reg
    out["registrations"] = event_registrations_collection.count_documents({"event_id": e["_id"]})
    out["full"] = bool(e.get("capacity")) and out["registrations"] >= e["capacity"]
    end = _aware(e.get("ends_at") or e["starts_at"])
    out["completed"] = end < now()
    if not (reg and with_link):
        out.pop("join_url", None)
    return out


@router.get("/events")
def list_events(when: Literal["upcoming", "past", "all"] = "upcoming", type: Optional[str] = Query(default=None, pattern="^(webinar|workshop|seminar|career_fair|networking)$"), page: int = Query(1, ge=1), limit: int = Query(12, ge=1, le=50), user=Depends(get_current_user)):
    q: dict = {"is_published": True}
    if type:
        q["type"] = type
    t = now()
    if when == "upcoming":
        q["starts_at"] = {"$gte": t - timedelta(hours=3)}
    elif when == "past":
        q["starts_at"] = {"$lt": t - timedelta(hours=3)}
    mine = {r["event_id"] for r in event_registrations_collection.find({"user_id": user["_id"]})}
    return paginate(events_collection, q, page, limit, [("starts_at", 1 if when != "past" else -1)], mapper=lambda e: _event_out(e, user["_id"], mine))


@router.get("/events/mine")
def my_events(user=Depends(get_current_user)):
    regs = list(event_registrations_collection.find({"user_id": user["_id"]}))
    ids = [r["event_id"] for r in regs]
    mine = set(ids)
    events = list(events_collection.find({"_id": {"$in": ids}}).sort("starts_at", 1))
    maybe_send_reminders(user["_id"], events)
    items = [_event_out(e, user["_id"], mine) for e in events]
    return {"upcoming": [e for e in items if not e["completed"]], "completed": [e for e in items if e["completed"]][::-1]}


@router.post("/events/{event_id}/register", status_code=201)
def register_event(event_id: str, user=Depends(get_current_user)):
    e = get_or_404(events_collection, oid(event_id, "event ID"), "Event", {"is_published": True})
    if _aware(e.get("ends_at") or e["starts_at"]) < now():
        raise HTTPException(400, "This event has already finished")
    if event_registrations_collection.find_one({"event_id": e["_id"], "user_id": user["_id"]}):
        return {"message": "You are already registered", "already_registered": True}
    if e.get("capacity") and event_registrations_collection.count_documents({"event_id": e["_id"]}) >= e["capacity"]:
        raise HTTPException(409, "This event is full")
    try:
        event_registrations_collection.insert_one({"event_id": e["_id"], "user_id": user["_id"], "created_at": now(), "reminded": False})
    except Exception:
        return {"message": "You are already registered", "already_registered": True}
    notify(user["_id"], f"Registered: {e['title']}", f"{_aware(e['starts_at']):%d %b %Y, %H:%M} UTC. We will remind you a day before.", "system", "/events")
    return {"message": "You are registered", "already_registered": False, "join_url": e.get("join_url")}


@router.delete("/events/{event_id}/register")
def unregister_event(event_id: str, user=Depends(get_current_user)):
    event_registrations_collection.delete_one({"event_id": oid(event_id, "event ID"), "user_id": user["_id"]})
    return {"message": "Registration cancelled"}


def maybe_send_reminders(uid, events: list) -> int:
    n = 0
    for e in events:
        st = _aware(e["starts_at"])
        if now() < st <= now() + timedelta(hours=24):
            r = event_registrations_collection.find_one_and_update({"event_id": e["_id"], "user_id": uid, "reminded": {"$ne": True}}, {"$set": {"reminded": True}})
            if r:
                notify(uid, f"Reminder: {e['title']} starts soon", f"Starts {st:%d %b, %H:%M} UTC.", "system", "/events")
                n += 1
    return n


def run_event_reminders() -> int:
    n = 0
    soon = list(events_collection.find({"starts_at": {"$gt": now(), "$lte": now() + timedelta(hours=24)}, "is_published": True}))
    for e in soon:
        for r in event_registrations_collection.find({"event_id": e["_id"], "reminded": {"$ne": True}}):
            n += maybe_send_reminders(r["user_id"], [e])
    return n


# =========================================================== offers (candidate side)
@router.get("/me/offers")
def my_offers(user=Depends(get_current_user)):
    rows = offers_collection.find({"candidate_id": user["_id"], "status": {"$ne": "draft"}}).sort("created_at", -1)
    return {"items": [offer_out(o) for o in rows]}


@router.get("/me/offers/{offer_id}")
def my_offer(offer_id: str, user=Depends(get_current_user)):
    o = get_or_404(offers_collection, oid(offer_id, "offer ID"), "Offer", {"candidate_id": user["_id"], "status": {"$ne": "draft"}})
    return offer_out(o, with_letter=True)


@router.get("/me/offers/{offer_id}/pdf")
def my_offer_pdf(offer_id: str, user=Depends(get_current_user)):
    o = get_or_404(offers_collection, oid(offer_id, "offer ID"), "Offer", {"candidate_id": user["_id"], "status": {"$ne": "draft"}})
    title, paras = letter_paragraphs(o)
    return Response(simplepdf.build(title, paras, footer=o["company_name"]), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=offer-{str(o['_id'])[-6:]}.pdf"})


class RespondIn(BaseModel):
    decision: Literal["accept", "decline"]
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/me/offers/{offer_id}/respond")
def respond_offer(offer_id: str, data: RespondIn, user=Depends(get_current_user)):
    o = get_or_404(offers_collection, oid(offer_id, "offer ID"), "Offer", {"candidate_id": user["_id"]})
    if o["status"] != "sent":
        raise HTTPException(400, f"This offer is {o['status']} and can no longer be answered")
    exp = _aware(o.get("expires_at"))
    if exp and exp < now():
        offers_collection.update_one({"_id": o["_id"]}, {"$set": {"status": "expired"}, "$push": {"history": {"status": "expired", "at": now()}}})
        raise HTTPException(400, "This offer has expired")
    status = "accepted" if data.decision == "accept" else "declined"
    offers_collection.update_one({"_id": o["_id"]}, {"$set": {"status": status, "responded_at": now(), "response_note": data.note, "updated_at": now()}, "$push": {"history": {"status": status, "at": now(), "note": data.note}}})
    if status == "accepted":
        applications_collection.update_one({"_id": o["application_id"]}, {"$set": {"status": "accepted", "updated_at": now()}, "$push": {"status_history": {"status": "accepted", "at": now(), "note": "Offer accepted"}}})
    notify(o["recruiter_id"], f"Offer {status}", f"{user['name']} {status} your offer for {o['designation']}.", "application", "/recruiter/offers")
    return offer_out(offers_collection.find_one({"_id": o["_id"]}), with_letter=True)


# =========================================================== followed companies feed
@router.get("/me/following/companies")
def followed_companies(user=Depends(get_current_user)):
    rows = list(followers_collection.find({"follower_id": user["_id"], "type": "company"}).sort("created_at", -1))
    out = []
    for r in rows:
        c = companies_collection.find_one({"_id": r["following_id"]})
        if not c:
            continue
        open_jobs = list(jobs_collection.find({"company_id": c["_id"], **public_visibility()}).sort("created_at", -1).limit(3))
        out.append({
            "id": str(c["_id"]), "name": c["name"], "logo_url": c.get("logo_url"), "industry": c.get("industry"), "location": c.get("location"), "description": (c.get("description") or "")[:200],
            "followed_at": ser(r["created_at"]), "open_jobs": jobs_collection.count_documents({"company_id": c["_id"], **public_visibility()}),
            "latest_jobs": [{"id": str(j["_id"]), "title": j["title"], "created_at": ser(j["created_at"]), "location": j.get("location")} for j in open_jobs],
            "updated_at": ser(c.get("updated_at")),
        })
    return {"items": out, "total": len(out)}


def notify_followers_of_job(job: dict) -> int:
    """New approved job -> everyone following that company gets an in-app notification."""
    n = 0
    company = companies_collection.find_one({"_id": job.get("company_id")}) or {}
    for f in followers_collection.find({"following_id": job.get("company_id"), "type": "company"}):
        notify(f["follower_id"], f"{company.get('name', 'A company you follow')} is hiring", job["title"], "job", f"/jobs/{job['_id']}")
        n += 1
    return n
