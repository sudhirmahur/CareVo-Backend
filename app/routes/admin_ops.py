"""Admin operations: revenue & growth analytics, system health, data exports, backups."""
import csv
import io
import platform
import shutil
import time
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import config
from app.database import (
    applications_collection,
    backups_collection,
    db,
    health_checks_collection,
    jobs_collection,
    payments_collection,
    subscriptions_collection,
    system_errors_collection,
    users_collection,
)
from app.dependencies.auth import get_current_user, require_admin
from app.services import backup as bk
from app.services import simplepdf, xlsxlite
from app.services.audit import audit
from app.utils import get_or_404, now, oid, ser

router = APIRouter(dependencies=[Depends(require_admin)])
me_router = APIRouter()  # any signed-in administrator may read their own effective permissions
STARTED = time.time()


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


@me_router.get("/me/permissions")
def my_permissions(user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "You do not have permission for this action")
    from app.services.permissions import MODULES, can, effective

    role = user.get("admin_role") or "super_admin"
    perms = {m: {a: can(user, m, a) for a in ("read", "create", "update", "delete")} for m in MODULES}
    return {"role": role, "is_super_admin": role == "super_admin" and not user.get("admin_permissions"), "permissions": perms}


# ============================================================ revenue & growth analytics
def _bucket(d: datetime, period: str) -> str:
    d = _aware(d)
    if period == "weekly":
        d = d - timedelta(days=d.weekday())
        return f"{d:%Y-%m-%d}"
    if period == "monthly":
        return f"{d:%Y-%m}"
    return f"{d:%Y-%m-%d}"


def _labels(start: datetime, end: datetime, period: str) -> list[str]:
    out, d = [], start
    while d <= end:
        b = _bucket(d, period)
        if not out or out[-1] != b:
            out.append(b)
        d += timedelta(days=1)
    return out


def revenue_report(period: str, days: int) -> dict:
    end = now()
    start = (end - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    prev_start = start - timedelta(days=days)
    labels = _labels(start, end, period)
    series = {k: {l: 0 for l in labels} for k in ("revenue", "orders", "refunds", "users", "recruiters", "jobs", "applications")}

    def add(key, d, amt=1):
        b = _bucket(d, period)
        if b in series[key]:
            series[key][b] += amt

    cur_rev = prev_rev = cur_users = prev_users = 0.0
    for p in payments_collection.find({"status": {"$in": ["paid", "refunded"]}, "created_at": {"$gte": prev_start}}):
        t = _aware(p.get("paid_at") or p["created_at"])
        if t < start:
            if p["status"] == "paid":
                prev_rev += p["amount"]
            continue
        if p["status"] == "paid":
            add("revenue", t, p["amount"])
            add("orders", t)
            cur_rev += p["amount"]
        else:
            add("refunds", t, p["amount"])
    for u in users_collection.find({"created_at": {"$gte": prev_start}, "role": {"$in": ["user", "recruiter"]}}, {"role": 1, "created_at": 1}):
        t = _aware(u["created_at"])
        if t < start:
            prev_users += 1
            continue
        cur_users += 1
        add("users" if u["role"] == "user" else "recruiters", t)
    for j in jobs_collection.find({"created_at": {"$gte": start}}, {"created_at": 1}):
        add("jobs", j["created_at"])
    for a in applications_collection.find({"applied_at": {"$gte": start}}, {"applied_at": 1}):
        add("applications", a["applied_at"])
    pct = lambda c, p: round((c - p) / p * 100, 1) if p else None
    paying = {s["user_id"] for s in subscriptions_collection.find({"ends_at": {"$gt": end}})}
    total_users = users_collection.count_documents({"role": "user"})
    return {
        "period": period,
        "days": days,
        "labels": labels,
        "series": {k: [v[l] for l in labels] for k, v in series.items()},
        "totals": {
            "revenue": cur_rev,
            "orders": int(sum(series["orders"].values())),
            "refunds": sum(series["refunds"].values()),
            "new_users": int(cur_users),
            "active_subscribers": len(paying),
            "conversion_rate": round(len(paying) / total_users * 100, 1) if total_users else 0.0,
            "arpu": round(cur_rev / cur_users, 2) if cur_users else 0.0,
        },
        "growth": {"revenue_pct": pct(cur_rev, prev_rev), "users_pct": pct(cur_users, prev_users), "previous_revenue": prev_rev, "previous_users": int(prev_users)},
    }


@router.get("/analytics/revenue")
def analytics_revenue(period: Literal["daily", "weekly", "monthly"] = "daily", days: int = Query(30, ge=1, le=366)):
    return revenue_report(period, days)


@router.get("/analytics/revenue/export")
def analytics_revenue_export(period: Literal["daily", "weekly", "monthly"] = "daily", days: int = Query(30, ge=1, le=366)):
    r = revenue_report(period, days)
    keys = list(r["series"])
    return _csv_response([[l] + [r["series"][k][i] for k in keys] for i, l in enumerate(r["labels"])], ["Period"] + [k.title() for k in keys], f"revenue-{period}.csv")


# ============================================================ system health
def _check_db() -> dict:
    t = time.perf_counter()
    try:
        users_collection.find_one({}, {"_id": 1})
        return {"status": "ok", "latency_ms": round((time.perf_counter() - t) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "down", "latency_ms": None, "error": str(exc)[:160]}


def health_snapshot(save: bool = True) -> dict:
    dbc = _check_db()
    try:
        du = shutil.disk_usage(str(config.UPLOAD_DIR))
        disk = {"total_gb": round(du.total / 2**30, 1), "used_pct": round(du.used / du.total * 100, 1)}
    except OSError:
        disk = {"total_gb": None, "used_pct": None}
    since = now() - timedelta(hours=24)
    errors24 = system_errors_collection.count_documents({"at": {"$gte": since}})
    alerts = []
    if dbc["status"] != "ok":
        alerts.append({"level": "critical", "message": "Database is not responding"})
    elif dbc["latency_ms"] > 500:
        alerts.append({"level": "warning", "message": f"Database is slow ({dbc['latency_ms']} ms)"})
    if disk["used_pct"] and disk["used_pct"] > 90:
        alerts.append({"level": "critical", "message": f"Disk is {disk['used_pct']}% full"})
    if errors24 >= 20:
        alerts.append({"level": "warning", "message": f"{errors24} server errors in the last 24 hours"})
    last = backups_collection.find_one({"status": "success"}, sort=[("created_at", -1)])
    if not last or now() - _aware(last["created_at"]) > timedelta(days=8):
        alerts.append({"level": "info", "message": "No recent backup (older than 8 days)"})
    status = "down" if dbc["status"] != "ok" else ("degraded" if any(a["level"] != "info" for a in alerts) else "ok")
    snap = {
        "status": status,
        "api": {"status": "ok", "uptime_seconds": int(time.time() - STARTED), "python": platform.python_version()},
        "database": dbc,
        "disk": disk,
        "errors_24h": errors24,
        "services": {
            "email": "smtp" if config.SMTP_HOST else "outbox only",
            "payments": "configured" if config.RAZORPAY_KEY_ID else "not configured",
            "media": "cloudinary" if config.CLOUDINARY_CLOUD_NAME else "local disk",
            "scheduler": "on" if config.SCHEDULER_ENABLED else "off",
        },
        "counts": {"users": users_collection.count_documents({}), "jobs": jobs_collection.count_documents({}), "applications": applications_collection.count_documents({})},
        "last_backup": ser(last["created_at"]) if last else None,
        "alerts": alerts,
        "at": now(),
    }
    if save:
        health_checks_collection.insert_one({k: snap[k] for k in ("status", "database", "errors_24h", "at")} | {"uptime": snap["api"]["uptime_seconds"]})
        old = list(health_checks_collection.find({}, {"_id": 1}).sort("at", -1).skip(500))
        if old:
            health_checks_collection.delete_many({"_id": {"$in": [o["_id"] for o in old]}})
    return ser(snap)


@router.get("/offers")
def admin_offers(status: Optional[str] = None, q: Optional[str] = Query(None, max_length=80), page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    """All offer letters on the platform (read-only oversight; recruiters create and send them)."""
    from app.database import offers_collection
    from app.services.offers import offer_out
    import re as _re

    f = {}
    if status:
        f["status"] = status
    if q:
        rx = {"$regex": _re.escape(q.strip()), "$options": "i"}
        f["$or"] = [{"candidate_name": rx}, {"company_name": rx}, {"designation": rx}]
    total = offers_collection.count_documents(f)
    rows = list(offers_collection.find(f).sort("created_at", -1).skip((page - 1) * limit).limit(limit))
    counts = {}
    for r in offers_collection.find({}, {"status": 1}):
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"items": [offer_out(r) for r in rows], "total": total, "page": page, "pages": max(1, -(-total // limit)), "counts": counts}


@router.get("/offers/{offer_id}/pdf")
def admin_offer_pdf(offer_id: str):
    from fastapi.responses import Response
    from app.database import offers_collection
    from app.services.offers import render_pdf

    o = get_or_404(offers_collection, oid(offer_id, "offer ID"), "Offer")
    return Response(render_pdf(o), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=offer-{str(o['_id'])[-6:]}.pdf", "Cache-Control": "private, no-store"})


@router.get("/media/diagnostics")
def media_diagnostics():
    """Why might reels not play? Shows storage provider and finds videos whose file no longer exists
    (typical after a deploy when files were kept on the server's temporary disk)."""
    from app.database import videos_collection
    from app.services import storage

    rows = list(videos_collection.find({"is_removed": {"$ne": True}}, {"title": 1, "video_url": 1, "thumbnail_url": 1}))
    summary = {"cloudinary": 0, "local": 0, "external": 0}
    broken = []
    for v in rows:
        url = v.get("video_url") or ""
        if url.startswith("/uploads/"):
            summary["local"] += 1
            path = (config.UPLOAD_DIR / url[len("/uploads/"):]).resolve()
            if not str(path).startswith(str(config.UPLOAD_DIR.resolve())) or not path.is_file():
                broken.append({"id": str(v["_id"]), "title": v.get("title"), "video_url": url, "reason": "File is missing on the server (temporary disk was reset). Re-upload it."})
        elif "res.cloudinary.com" in url:
            summary["cloudinary"] += 1
        else:
            summary["external"] += 1
    return {
        "provider": storage.provider(),
        "cloudinary_configured": storage.cloudinary_enabled(),
        "videos": len(rows),
        "by_storage": summary,
        "missing": broken,
        "advice": None if storage.cloudinary_enabled() else "Cloudinary is not configured: uploads go to the server disk, which is erased on every Render deploy. Set CLOUDINARY_URL.",
    }


@router.get("/health")
def health():
    return health_snapshot()


@router.get("/health/history")
def health_history(limit: int = Query(50, ge=1, le=200)):
    rows = list(health_checks_collection.find({}).sort("at", -1).limit(limit))
    return {"items": [ser(r) for r in rows], "total": len(rows)}


@router.get("/health/errors")
def health_errors(limit: int = Query(50, ge=1, le=200)):
    rows = list(system_errors_collection.find({}).sort("at", -1).limit(limit))
    return {"items": [ser(r) for r in rows], "total": system_errors_collection.count_documents({})}


@router.delete("/health/errors")
def health_errors_clear(admin=Depends(require_admin)):
    n = system_errors_collection.delete_many({}).deleted_count
    audit(admin, "health.errors.clear", "", str(n))
    return {"deleted": n}


def record_error(method: str, path: str, exc: Exception) -> None:
    try:
        system_errors_collection.insert_one({"method": method, "path": path[:200], "error": type(exc).__name__, "message": str(exc)[:300], "at": now()})
    except Exception:  # noqa: BLE001 - never mask the original failure
        pass


# ============================================================ data export (CSV / Excel / PDF)
def _csv_response(rows, header, filename) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([("'" + c) if isinstance(c, str) and c[:1] in "=+-@" else c for c in r])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _day(d) -> str:
    return f"{_aware(d):%Y-%m-%d}" if d else ""


def _dataset(name: str) -> tuple[list[str], list[list]]:
    if name in ("users", "recruiters"):
        role = "user" if name == "users" else "recruiter"
        rows = users_collection.find({"role": role}).sort("created_at", -1)
        return ["Name", "Email", "Phone", "Active", "Premium", "Joined"], [[u["name"], u["email"], u.get("phone") or "", "yes" if u.get("is_active", True) else "no", "yes" if u.get("is_premium") else "no", _day(u.get("created_at"))] for u in rows]
    if name == "jobs":
        rows = jobs_collection.find({}).sort("created_at", -1)
        return ["Title", "Company", "Type", "Location", "Status", "Views", "Applications", "Posted"], [[j["title"], j.get("company_name") or "", j.get("job_type", ""), j.get("location") or "", j.get("status", ""), j.get("views_count", 0), j.get("applications_count", 0), _day(j.get("created_at"))] for j in rows]
    if name == "applications":
        apps = list(applications_collection.find({}).sort("applied_at", -1).limit(20000))
        users = {u["_id"]: u for u in users_collection.find({"_id": {"$in": [a["user_id"] for a in apps]}}, {"name": 1, "email": 1})}
        return ["Candidate", "Email", "Job", "Company", "Status", "Applied"], [[users.get(a["user_id"], {}).get("name", ""), users.get(a["user_id"], {}).get("email", ""), a.get("job_title", ""), a.get("company_name") or "", a["status"], _day(a.get("applied_at"))] for a in apps]
    if name == "payments":
        rows = payments_collection.find({}).sort("created_at", -1)
        return ["Order", "Plan", "Amount (INR)", "Status", "Created"], [[p["order_id"], p.get("plan_name", ""), p["amount"], p["status"], _day(p.get("created_at"))] for p in rows]
    raise HTTPException(404, "Unknown dataset")


DATASETS = ["users", "recruiters", "jobs", "applications", "payments"]


@router.get("/exports")
def exports_info():
    return {"datasets": [{"key": k, "count": len(_dataset(k)[1])} for k in DATASETS], "formats": ["csv", "xlsx", "pdf"]}


@router.get("/exports/{dataset}")
def export_dataset(dataset: str, format: Literal["csv", "xlsx", "pdf"] = "csv", admin=Depends(require_admin)):
    header, rows = _dataset(dataset)
    audit(admin, "export.data", dataset, f"{format}, {len(rows)} rows")
    fname = f"carevo-{dataset}-{now():%Y%m%d}"
    if format == "csv":
        return _csv_response(rows, header, fname + ".csv")
    if format == "xlsx":
        return Response(xlsxlite.build(header, rows, dataset.title()), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'})
    lines = [" | ".join(header)] + [" | ".join(str(c) for c in r) for r in rows[:1000]]
    if len(rows) > 1000:
        lines.append(f"... {len(rows) - 1000} more rows - use CSV/Excel for the full export")
    return Response(simplepdf.build(f"CareVo - {dataset.title()} ({len(rows)})", ["\n".join(lines)], footer=f"Exported {now():%d %b %Y %H:%M} UTC"), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'})


# ============================================================ backups
def _super(admin: dict) -> None:
    if admin.get("admin_role") not in (None, "super_admin"):
        raise HTTPException(403, "Only a Super Admin can do this")


class ScheduleIn(BaseModel):
    enabled: bool
    frequency: Literal["daily", "weekly"] = "daily"
    keep: int = Field(default=7, ge=1, le=60)


class RestoreIn(BaseModel):
    confirm: str


@router.get("/backups")
def backups_list():
    rows = list(backups_collection.find({}).sort("created_at", -1).limit(100))
    return {"items": [ser(r) for r in rows], "total": len(rows), "schedule": ser(bk.schedule())}


@router.post("/backups", status_code=201)
def backup_now(admin=Depends(require_admin)):
    row = bk.create("manual", admin.get("email", "admin"))
    audit(admin, "backup.create", str(row["_id"]), row["status"])
    if row["status"] != "success":
        raise HTTPException(500, "Backup failed: " + row.get("error", "unknown error"))
    return ser(row)


@router.put("/backups/schedule")
def backup_schedule(data: ScheduleIn, admin=Depends(require_admin)):
    audit(admin, "backup.schedule", "", f"{data.enabled} {data.frequency} keep {data.keep}")
    return ser(bk.set_schedule(data.model_dump()))


@router.post("/backups/{backup_id}/restore")
def backup_restore(backup_id: str, data: RestoreIn, admin=Depends(require_admin)):
    _super(admin)
    row = get_or_404(backups_collection, oid(backup_id, "backup ID"), "Backup", {"status": "success"})
    if data.confirm != "RESTORE":
        raise HTTPException(422, "Type RESTORE to confirm. This replaces ALL current data.")
    safety = bk.create("pre-restore", admin.get("email", "admin"))  # always keep a way back
    try:
        res = bk.restore(row)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc))
    audit(admin, "backup.restore", str(row["_id"]), f"{res['documents']} documents")
    return {"message": "Backup restored", **res, "safety_backup": str(safety["_id"])}


@router.delete("/backups/{backup_id}")
def backup_delete(backup_id: str, admin=Depends(require_admin)):
    _super(admin)
    row = get_or_404(backups_collection, oid(backup_id, "backup ID"), "Backup")
    bk.delete(row)
    audit(admin, "backup.delete", str(row["_id"]))
    return {"message": "Backup deleted"}
