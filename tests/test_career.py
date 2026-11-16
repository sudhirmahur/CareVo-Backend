from datetime import datetime, timedelta, timezone

from tests.conftest import JOB

PDF = b"%PDF-1.4 hello"


def iso(days=0, hours=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


def _job(recruiter, admin, **extra):
    r = recruiter.post("/api/recruiter/jobs", json={**JOB, "apply_url": "https://x.test/a", **extra})
    assert r.status_code == 201, r.text
    admin.patch(f"/api/admin/jobs/{r.json()['id']}/status", json={"status": "approved"})
    return r.json()


def test_job_preferences_matches_and_digest(recruiter, admin, make_actor):
    from app.routes.career import run_alert_digests

    u = make_actor("user")
    assert u.get("/api/me/job-matches").json()["configured"] is False
    p = {"titles": ["react"], "locations": ["remote"], "job_types": ["fulltime", "freelance"], "work_modes": ["remote"], "alerts_enabled": True, "frequency": "daily", "salary_min": 500000, "salary_max": 2000000}
    assert u.put("/api/me/preferences", json={**p, "salary_min": 9, "salary_max": 1}).status_code == 422
    assert u.put("/api/me/preferences", json={"alerts_enabled": True}).status_code == 422  # nothing chosen
    assert u.put("/api/me/preferences", json={**p, "job_types": ["nonsense"]}).status_code == 422
    r = u.put("/api/me/preferences", json=p)
    assert r.status_code == 200 and r.json()["titles"] == ["react"]
    assert u.get("/api/me/preferences").json()["alerts_enabled"] is True
    _job(recruiter, admin, work_mode="remote")
    _job(recruiter, admin, title="Plumber", description="Fix pipes in houses all day.", skills=[], location="Pune", work_mode="onsite")
    m = u.get("/api/me/job-matches").json()
    assert [j["title"] for j in m["items"]] == ["Senior React Developer"]
    from app.database import users_collection
    from bson import ObjectId

    users_collection.update_one({"_id": ObjectId(u.id)}, {"$set": {"job_preferences.last_alert_at": datetime.now(timezone.utc) - timedelta(days=2)}})
    assert run_alert_digests() == 1
    assert run_alert_digests() == 0  # not twice in the same period
    assert any("match your alerts" in n["title"] for n in u.get("/api/notifications").json()["items"])
    assert make_actor("recruiter").get("/api/me/preferences").status_code == 403


def test_document_vault(make_actor, client):
    u, v = make_actor("user"), make_actor("user")
    up = lambda name, data, cat="certificate", t="": u.post("/api/me/documents", files={"file": (name, data, "application/octet-stream")}, data={"category": cat, "title": t})
    assert up("evil.exe", b"MZ").status_code == 400
    assert up("fake.pdf", b"not a pdf").status_code == 400
    assert up("empty.pdf", b"").status_code == 400
    assert up("big.pdf", PDF + b"0" * (9 * 1024 * 1024)).status_code == 400
    assert up("c.pdf", PDF, "weird").status_code == 422
    d = up("../../etc/passwd.pdf", PDF, "certificate", "AWS Cert")
    assert d.status_code == 201, d.text
    d = d.json()
    assert d["title"] == "AWS Cert" and d["category"] == "certificate" and "stored_name" not in d and "/" not in d["filename"]
    up("id.png", b"\x89PNG....", "other")
    assert u.get("/api/me/documents").json()["counts"]["other"] == 1
    assert len(u.get("/api/me/documents", params={"category": "certificate"}).json()["items"]) == 1
    assert u.patch(f"/api/me/documents/{d['id']}", json={"category": "education", "title": "Degree"}).json()["category"] == "education"
    link = u.get(f"/api/me/documents/{d['id']}/link").json()
    f = client.get(link["url"])
    assert f.status_code == 200 and f.content == PDF
    assert client.get(link["url"].rsplit("sig=", 1)[0] + "sig=" + "0" * 64).status_code == 403
    assert client.get(link["url"].replace("exp=", "exp=1").split("&")[0] + "&sig=x").status_code == 403
    assert v.get(f"/api/me/documents/{d['id']}/link").status_code == 404
    assert v.patch(f"/api/me/documents/{d['id']}", json={"title": "x"}).status_code == 404
    assert v.delete(f"/api/me/documents/{d['id']}").status_code == 404
    r = u.put(f"/api/me/documents/{d['id']}/file", files={"file": ("new.pdf", PDF + b"v2", "application/pdf")})
    assert r.status_code == 200
    assert client.get(u.get(f"/api/me/documents/{d['id']}/link").json()["url"]).content.endswith(b"v2")
    assert u.put(f"/api/me/documents/{d['id']}/file", files={"file": ("bad.pdf", b"zzz", "application/pdf")}).status_code == 400
    assert u.delete(f"/api/me/documents/{d['id']}").status_code == 200
    assert len(u.get("/api/me/documents").json()["items"]) == 1
    assert client.get(f"/api/files/doc/{d['id']}").status_code in (403, 422)


def test_referrals_and_rewards(client, make_actor):
    a = make_actor("user")
    ref = a.get("/api/me/referrals").json()
    code = ref["code"]
    assert code and f"ref={code}" in ref["link"] and ref["total"] == 0
    # unknown / own / recruiter codes are ignored, signup still works
    r = client.post("/api/auth/register", json={"name": "Zed Zed", "email": "z1@x.test", "password": "secret123", "role": "user", "referral_code": "NOPE"})
    assert r.status_code == 201
    for i in range(5):
        r = client.post("/api/auth/register", json={"name": f"Friend {i}", "email": f"f{i}@x.test", "password": "secret123", "role": "user", "referral_code": code.lower()})
        assert r.status_code == 201
    ref = a.get("/api/me/referrals").json()
    assert ref["total"] == 5 and ref["points"] == 250
    assert [x["unlocked"] for x in ref["rewards"]] == [True, True, True, False]
    me = a.get("/api/users/me").json()
    assert me["is_premium"] is True  # 5 referrals -> 7 days premium
    inv = a.post("/api/me/referrals/invite", json={"emails": ["newfriend@x.test", "f0@x.test", "newfriend@x.test"]})
    assert inv.status_code == 201 and inv.json()["sent"] == 1 and inv.json()["skipped"] == ["f0@x.test"]
    assert a.post("/api/me/referrals/invite", json={"emails": ["bad"]}).status_code == 422
    assert a.get("/api/me/referrals").json()["invited"] == 1


def test_career_events_end_to_end(admin, make_actor):
    u1, u2 = make_actor("user"), make_actor("user")
    ev = {"title": "Resume Webinar", "type": "webinar", "starts_at": iso(0, 5), "ends_at": iso(0, 6), "capacity": 1, "join_url": "https://meet.test/abc"}
    assert admin.post("/api/admin/events", json={**ev, "ends_at": iso(-1)}).status_code == 422
    assert u1.post("/api/admin/events", json=ev).status_code == 403
    e = admin.post("/api/admin/events", json=ev)
    assert e.status_code == 201, e.text
    eid = e.json()["id"]
    hidden = admin.post("/api/admin/events", json={**ev, "title": "Hidden event", "is_published": False}).json()
    assert [x["id"] for x in u1.get("/api/events").json()["items"]] == [eid]
    assert u1.post(f"/api/events/{hidden['id']}/register").status_code == 404
    r = u1.post(f"/api/events/{eid}/register")
    assert r.status_code == 201 and r.json()["already_registered"] is False
    assert u1.post(f"/api/events/{eid}/register").json()["already_registered"] is True
    assert u2.post(f"/api/events/{eid}/register").status_code == 409  # full
    assert admin.get(f"/api/admin/events/{eid}/registrations").json()["total"] == 1
    mine = u1.get("/api/events/mine").json()
    assert len(mine["upcoming"]) == 1 and mine["upcoming"][0]["join_url"] == "https://meet.test/abc"
    assert "join_url" not in u2.get("/api/events").json()["items"][0]
    from app.routes.career import run_event_reminders

    assert run_event_reminders() == 0  # /events/mine already sent the (once-only) reminder lazily
    assert any("Reminder" in n["title"] for n in u1.get("/api/notifications").json()["items"])
    assert admin.put(f"/api/admin/events/{eid}", json={"starts_at": iso(1), "ends_at": iso(1, 1)}).status_code == 200
    assert admin.put(f"/api/admin/events/{eid}", json={"starts_at": iso(3)}).status_code == 422  # would end before it starts
    assert any("Time changed" in n["title"] for n in u1.get("/api/notifications").json()["items"])
    assert u1.delete(f"/api/events/{eid}/register").status_code == 200
    assert u2.post(f"/api/events/{eid}/register").status_code == 201  # seat freed
    assert admin.delete(f"/api/admin/events/{eid}").status_code == 200
    assert u2.get("/api/events/mine").json()["upcoming"] == []
    past = admin.post("/api/admin/events", json={**ev, "title": "Old one", "starts_at": iso(-3), "ends_at": iso(-2)}).json()
    assert u1.post(f"/api/events/{past['id']}/register").status_code == 400
    assert [x["title"] for x in u1.get("/api/events", params={"when": "past"}).json()["items"]] == ["Old one"]


def test_follow_feed_and_new_job_notification(recruiter, admin, make_actor):
    u = make_actor("user")
    cid = recruiter.company["id"]
    assert u.post("/api/follow", json={"following_id": cid, "type": "company"}).status_code == 201
    _job(recruiter, admin)
    f = u.get("/api/me/following/companies").json()
    assert f["total"] == 1 and f["items"][0]["name"] == "Acme Corp" and f["items"][0]["open_jobs"] == 1
    assert f["items"][0]["latest_jobs"][0]["title"] == "Senior React Developer"
    assert any("Acme Corp" in n["title"] or "Acme Corp" in n.get("message", "") for n in u.get("/api/notifications").json()["items"])
    assert make_actor("user").get("/api/me/following/companies").json()["total"] == 0
