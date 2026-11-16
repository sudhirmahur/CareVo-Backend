import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import JOB


@pytest.fixture(autouse=True)
def backup_dir(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "bk")
    return tmp_path / "bk"


def _paid(user, amount, days_ago=0, status="paid"):
    from app.database import payments_collection
    from bson import ObjectId

    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    payments_collection.insert_one({"order_id": f"order_{ObjectId()}", "user_id": ObjectId(user.id), "plan_id": ObjectId(), "plan_name": "Pro", "duration_days": 30, "amount": amount, "currency": "INR", "status": status, "created_at": t, "paid_at": t, "updated_at": t})


def test_revenue_analytics_and_growth(admin, make_actor):
    u = make_actor("user")
    make_actor("recruiter")
    _paid(u, 499)
    _paid(u, 999, days_ago=2)
    _paid(u, 300, status="refunded")
    _paid(u, 1000, days_ago=40)  # previous window
    r = admin.get("/api/admin/analytics/revenue", params={"period": "daily", "days": 30}).json()
    assert r["totals"]["revenue"] == 1498 and r["totals"]["orders"] == 2 and r["totals"]["refunds"] == 300
    assert len(r["labels"]) == 30 and sum(r["series"]["revenue"]) == 1498
    assert r["totals"]["new_users"] == 2 and sum(r["series"]["recruiters"]) == 1
    assert r["growth"]["previous_revenue"] == 1000 and r["growth"]["revenue_pct"] == 49.8
    w = admin.get("/api/admin/analytics/revenue", params={"period": "weekly", "days": 30}).json()
    assert sum(w["series"]["revenue"]) == 1498 and len(w["labels"]) <= 6
    assert admin.get("/api/admin/analytics/revenue", params={"period": "hourly"}).status_code == 422
    assert admin.get("/api/admin/analytics/revenue", params={"days": 0}).status_code == 422
    c = admin.get("/api/admin/analytics/revenue/export")
    assert c.headers["content-type"].startswith("text/csv") and c.text.startswith("Period,Revenue")
    assert u.get("/api/admin/analytics/revenue").status_code == 403


def test_system_health_and_error_tracking(admin, make_actor, client):
    h = admin.get("/api/admin/health").json()
    assert h["status"] in ("ok", "degraded") and h["database"]["status"] == "ok" and h["api"]["uptime_seconds"] >= 0
    assert h["services"]["scheduler"] == "off" and h["services"]["payments"] in ("configured", "not configured")
    assert any("backup" in a["message"].lower() for a in h["alerts"])
    assert admin.get("/api/admin/health/history").json()["total"] >= 1
    # an unhandled exception is recorded and does not leak details
    from app.main import app

    @app.get("/api/_boom")
    def boom():
        raise RuntimeError("secret internal detail")

    from fastapi.testclient import TestClient

    r = TestClient(app, raise_server_exceptions=False).get("/api/_boom")
    assert r.status_code == 500 and "secret" not in r.text
    errs = admin.get("/api/admin/health/errors").json()
    assert errs["total"] == 1 and errs["items"][0]["path"] == "/api/_boom" and errs["items"][0]["error"] == "RuntimeError"
    assert admin.delete("/api/admin/health/errors").json()["deleted"] == 1
    assert make_actor("user").get("/api/admin/health").status_code == 403


def test_exports_csv_xlsx_pdf(admin, make_actor, recruiter):
    make_actor("user", "=HYPERLINK(evil)")
    make_actor("user", "Normal Person")
    info = {d["key"]: d["count"] for d in admin.get("/api/admin/exports").json()["datasets"]}
    assert info["users"] == 2 and info["recruiters"] == 1
    c = admin.get("/api/admin/exports/users")
    assert c.status_code == 200 and "Normal Person" in c.text and "'=HYPERLINK" in c.text
    x = admin.get("/api/admin/exports/users", params={"format": "xlsx"})
    z = zipfile.ZipFile(io.BytesIO(x.content))
    sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert "Normal Person" in sheet and "'=HYPERLINK" in sheet and "xl/workbook.xml" in z.namelist()
    p = admin.get("/api/admin/exports/jobs", params={"format": "pdf"})
    assert p.content.startswith(b"%PDF") and p.headers["content-type"] == "application/pdf"
    assert admin.get("/api/admin/exports/secrets").status_code == 404
    assert admin.get("/api/admin/exports/users", params={"format": "exe"}).status_code == 422
    for d in ("applications", "payments", "jobs", "recruiters"):
        assert admin.get(f"/api/admin/exports/{d}").status_code == 200
    assert any(a["action"] == "export.data" for a in admin.get("/api/admin/audit-logs").json()["items"])
    assert make_actor("recruiter").get("/api/admin/exports/users").status_code == 403


def test_backup_create_schedule_restore(admin, make_actor, backup_dir, client):
    keep = make_actor("user", "Keep Me")
    b = admin.post("/api/admin/backups")
    assert b.status_code == 201 and b.json()["status"] == "success" and b.json()["documents"] > 0
    assert (backup_dir / b.json()["filename"]).is_file()
    assert admin.get("/api/admin/backups").json()["total"] == 1
    # data changes after the backup
    from app.database import users_collection
    from bson import ObjectId

    users_collection.delete_one({"_id": ObjectId(keep.id)})
    assert users_collection.find_one({"email": keep.user["email"]}) is None
    assert admin.post(f"/api/admin/backups/{b.json()['id']}/restore", json={"confirm": "yes"}).status_code == 422
    r = admin.post(f"/api/admin/backups/{b.json()['id']}/restore", json={"confirm": "RESTORE"})
    assert r.status_code == 200 and r.json()["documents"] > 0 and r.json()["safety_backup"]
    assert users_collection.find_one({"email": keep.user["email"]})["name"] == "Keep Me"
    assert client.post("/api/auth/login", json={"email": keep.user["email"], "password": "secret123"}).status_code == 200
    assert admin.get("/api/admin/backups").json()["total"] == 2  # original + automatic pre-restore safety copy
    # schedule + retention
    s = admin.put("/api/admin/backups/schedule", json={"enabled": True, "frequency": "weekly", "keep": 2})
    assert s.status_code == 200 and s.json()["keep"] == 2
    assert admin.put("/api/admin/backups/schedule", json={"enabled": True, "keep": 0}).status_code == 422
    from app.services import backup

    assert backup.run_if_due() is True and backup.run_if_due() is False
    rows = admin.get("/api/admin/backups").json()["items"]
    assert len([x for x in rows if x["status"] == "success"]) == 2 and any(x["kind"] == "scheduled" for x in rows)
    # missing file -> clean error, data untouched
    target = rows[-1]
    (backup_dir / target["filename"]).unlink()
    assert admin.post(f"/api/admin/backups/{target['id']}/restore", json={"confirm": "RESTORE"}).status_code == 409
    assert admin.delete(f"/api/admin/backups/{target['id']}").status_code == 200
    assert make_actor("user").post("/api/admin/backups").status_code == 403


def test_restore_and_delete_need_super_admin(admin, client):
    b = admin.post("/api/admin/backups").json()
    from app.database import users_collection
    from bson import ObjectId

    users_collection.update_one({"_id": ObjectId(admin.id)}, {"$set": {"admin_role": "admin"}})  # plain Admin role
    assert admin.post(f"/api/admin/backups/{b['id']}/restore", json={"confirm": "RESTORE"}).status_code == 403
    assert admin.delete(f"/api/admin/backups/{b['id']}").status_code == 403


def test_scheduler_tick_runs_tasks_once(admin):
    from app.services import scheduler

    scheduler._last.clear()
    first = scheduler.tick()
    assert set(first) == {"reminders", "digests", "backup"}
    assert scheduler.tick() == {}  # nothing due again immediately


def test_admin_me_permissions(admin, make_actor):
    r = admin.get("/api/admin/me/permissions").json()
    assert r["is_super_admin"] is True and r["role"] == "super_admin" and r["permissions"]["system"]["delete"] is True
    from app.database import users_collection
    from bson import ObjectId

    users_collection.update_one({"_id": ObjectId(admin.id)}, {"$set": {"admin_role": "support_manager"}})
    r = admin.get("/api/admin/me/permissions").json()
    assert r["is_super_admin"] is False and r["permissions"]["support"]["update"] is True and r["permissions"]["users"]["read"] is True and r["permissions"]["system"]["read"] is False
    assert make_actor("user").get("/api/admin/me/permissions").status_code == 403
