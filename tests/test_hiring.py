from datetime import datetime, timedelta, timezone

from tests.conftest import JOB

PDF = ("cv.pdf", b"%PDF-1.4 resume", "application/pdf")


def _job(recruiter, admin, **extra):
    r = recruiter.post("/api/recruiter/jobs", json={**JOB, "apply_url": "https://x.test/a", **extra})
    assert r.status_code == 201, r.text
    admin.patch(f"/api/admin/jobs/{r.json()['id']}/status", json={"status": "approved"})
    return r.json()


def _apply(user, job, **body):
    r = user.post(f"/api/jobs/{job['id']}/apply", json=body)
    assert r.status_code == 201, r.text
    return r.json()["application_id"]


def iso(days=0, hours=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


def test_screening_questions_required_and_filter(recruiter, admin, make_actor):
    qs = [
        {"id": "q1", "type": "yesno", "question": "Can you relocate?", "required": True},
        {"id": "q2", "type": "choice", "question": "Notice period", "options": ["Immediate", "30 days"], "required": True},
        {"id": "q3", "type": "text", "question": "Why us?", "required": False},
    ]
    job = _job(recruiter, admin, screening_questions=qs, work_mode="hybrid", job_type="freelance")
    assert job["work_mode"] == "hybrid" and job["job_type"] == "freelance"
    a, b = make_actor("user"), make_actor("user")
    assert a.post(f"/api/jobs/{job['id']}/apply", json={}).status_code in (400, 422)
    assert a.post(f"/api/jobs/{job['id']}/apply", json={"answers": {"q1": "yes", "q2": "Weekend"}}).status_code in (400, 422)
    _apply(a, job, answers={"q1": "yes", "q2": "Immediate"})
    _apply(b, job, answers={"q1": "no", "q2": "30 days"})
    r = recruiter.get(f"/api/recruiter/jobs/{job['id']}/screening").json()
    assert r["total"] == 2 and len(r["questions"]) == 3
    r = recruiter.get(f"/api/recruiter/jobs/{job['id']}/screening", params={"question_id": "q1", "answer": "YES"}).json()
    assert r["total"] == 1 and r["items"][0]["answers"][0]["answer"] == "Yes"
    assert recruiter.get(f"/api/recruiter/jobs/{job['id']}/screening", params={"question_id": "zz"}).status_code == 404


def test_evaluations_compare_and_isolation(recruiter, admin, make_actor):
    job = _job(recruiter, admin)
    u1, u2 = make_actor("user"), make_actor("user")
    a1, a2 = _apply(u1, job), _apply(u2, job)
    ok = {"application_id": a1, "ratings": {"technical": 5, "communication": 3}, "recommendation": "yes", "comment": "Good", "private_note": "internal", "reviewer": "Ivan Interviewer"}
    r = recruiter.post("/api/recruiter/evaluations", json=ok)
    assert r.status_code == 201 and r.json()["average"] == 4.0
    assert recruiter.post("/api/recruiter/evaluations", json={**ok, "ratings": {"technical": 9}}).status_code == 422
    assert recruiter.post("/api/recruiter/evaluations", json={**ok, "ratings": {"bogus": 3}}).status_code == 422
    assert recruiter.post("/api/recruiter/evaluations", json={**ok, "ratings": {}}).status_code == 422
    recruiter.post("/api/recruiter/evaluations", json={**ok, "application_id": a2, "ratings": {"technical": 2}, "recommendation": "no"})
    lst = recruiter.get("/api/recruiter/evaluations", params={"application_id": a1}).json()
    assert lst["total"] == 1 and lst["overall"] == 4.0
    c = recruiter.get("/api/recruiter/evaluations/compare", params={"application_ids": f"{a1},{a2}"}).json()
    assert [i["overall"] for i in c["items"]] == [4.0, 2.0]
    assert recruiter.get("/api/recruiter/evaluations/compare", params={"application_ids": a1}).status_code == 422
    other = make_actor("recruiter")
    assert other.get("/api/recruiter/evaluations", params={"application_id": a1}).status_code == 404
    assert other.post("/api/recruiter/evaluations", json=ok).status_code == 404
    assert u1.get("/api/recruiter/evaluations", params={"application_id": a1}).status_code == 403
    eid = lst["items"][0]["id"]
    assert other.delete(f"/api/recruiter/evaluations/{eid}").status_code == 404
    assert recruiter.delete(f"/api/recruiter/evaluations/{eid}").status_code == 200


def test_calendar_views_reschedule_cancel_reminder(recruiter, admin, make_actor):
    job = _job(recruiter, admin)
    u = make_actor("user")
    app_id = _apply(u, job)
    assert recruiter.post("/api/recruiter/calendar", json={"title": "x", "starts_at": iso(1), "ends_at": iso(0)}).status_code == 422
    e = recruiter.post("/api/recruiter/calendar", json={"title": "Sync", "starts_at": iso(0, 1), "remind_minutes": 600}).json()
    recruiter.post("/api/recruiter/interviews", json={"application_id": app_id, "scheduled_at": iso(0, 2), "mode": "offline", "location": "HQ"})
    for view in ("day", "week", "month"):
        r = recruiter.get("/api/recruiter/calendar", params={"view": view}).json()
        kinds = {i["source"] for i in r["items"]}
        assert r["view"] == view
    assert kinds == {"event", "interview"} or view == "month"
    assert recruiter.get("/api/recruiter/calendar", params={"view": "year"}).status_code == 422
    assert recruiter.post("/api/recruiter/calendar/reminders/run").json()["sent"] == 1
    assert recruiter.post("/api/recruiter/calendar/reminders/run").json()["sent"] == 0  # once only
    r = recruiter.patch(f"/api/recruiter/calendar/{e['id']}", json={"starts_at": iso(3)})
    assert r.status_code == 200
    far = recruiter.get("/api/recruiter/calendar", params={"view": "day"}).json()
    assert all(i["id"] != e["id"] for i in far["items"])
    assert recruiter.patch(f"/api/recruiter/calendar/{e['id']}", json={"status": "cancelled"}).json()["status"] == "cancelled"
    other = make_actor("recruiter")
    assert other.patch(f"/api/recruiter/calendar/{e['id']}", json={"title": "hijack"}).status_code == 404
    assert other.delete(f"/api/recruiter/calendar/{e['id']}").status_code == 404
    assert recruiter.delete(f"/api/recruiter/calendar/{e['id']}").status_code == 200


def test_job_analytics_and_export(recruiter, admin, make_actor, client):
    job = _job(recruiter, admin)
    for src in ("linkedin", "linkedin", "direct"):
        assert client.get(f"/api/jobs/{job['id']}", params={"src": src}).status_code == 200
    recruiter.get(f"/api/jobs/{job['id']}")  # owner views are not counted
    u1, u2 = make_actor("user"), make_actor("user")
    a1 = _apply(u1, job)
    _apply(u2, job)
    recruiter.patch(f"/api/recruiter/applications/{a1}/status", json={"status": "shortlisted"})
    r = recruiter.get("/api/recruiter/analytics/jobs").json()
    t = r["totals"]
    assert t["views"] == 3 and t["applications"] == 2 and t["shortlisted"] == 1
    assert t["conversion"] == 66.7
    assert r["sources"][0] == {"source": "linkedin", "views": 2}
    assert recruiter.get("/api/recruiter/analytics/jobs", params={"from": iso(-60), "to": iso(-30)}).json()["totals"]["views"] == 0
    assert recruiter.get("/api/recruiter/analytics/jobs", params={"from": iso(1), "to": iso(-1)}).status_code == 422
    csv = recruiter.get("/api/recruiter/analytics/jobs/export")
    assert csv.headers["content-type"].startswith("text/csv") and "Senior React Developer,3,2,1" in csv.text
    other = make_actor("recruiter")
    assert other.get("/api/recruiter/analytics/jobs").json()["totals"]["views"] == 0


def test_team_management(recruiter, admin, make_actor):
    job = _job(recruiter, admin)
    r = recruiter.post("/api/recruiter/team", json={"name": "Ivan Iv", "email": "ivan@acme.test", "role": "interviewer", "job_ids": [job["id"]]})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["permissions"] == ["interviews", "evaluations"] and m["status"] == "invited"
    assert recruiter.post("/api/recruiter/team", json={"name": "Ivan Iv", "email": "IVAN@acme.test", "role": "recruiter"}).status_code == 409
    assert recruiter.post("/api/recruiter/team", json={"name": "Me", "email": recruiter.user["email"], "role": "recruiter"}).status_code == 409
    assert recruiter.post("/api/recruiter/team", json={"name": "Bad", "email": "nope", "role": "recruiter"}).status_code == 422
    assert recruiter.post("/api/recruiter/team", json={"name": "Owner", "email": "o@acme.test", "role": "owner"}).status_code == 422
    other = make_actor("recruiter")
    assert other.post("/api/recruiter/team", json={"name": "Zed Zed", "email": "z@z.test", "role": "recruiter", "job_ids": [job["id"]]}).status_code == 400  # no company
    assert recruiter.patch(f"/api/recruiter/team/{m['id']}", json={"permissions": ["root"]}).status_code == 422
    r = recruiter.patch(f"/api/recruiter/team/{m['id']}", json={"role": "hr_manager", "permissions": ["jobs"], "is_active": False})
    assert r.json()["role"] == "hr_manager" and r.json()["permissions"] == ["jobs"] and r.json()["is_active"] is False
    lst = recruiter.get("/api/recruiter/team").json()
    assert lst["total"] == 2 and lst["items"][0]["is_owner"]
    a = _apply(make_actor("user"), job)
    recruiter.patch(f"/api/recruiter/applications/{a}/status", json={"status": "accepted"})
    perf = recruiter.get(f"/api/recruiter/team/{m['id']}/performance").json()
    assert perf["assigned_jobs"] == 1 and perf["hired"] == 1 and perf["hire_rate"] == 100.0
    act = recruiter.get(f"/api/recruiter/team/{m['id']}/activity").json()
    assert act["items"][0]["action"].endswith("changed") and act["items"][-1]["action"] == "invited"
    assert admin.get("/api/recruiter/team").status_code in (200, 400)
    assert recruiter.delete(f"/api/recruiter/team/{m['id']}").status_code == 200
    assert recruiter.get("/api/recruiter/team").json()["total"] == 1


def test_bulk_csv_import_validation_and_actions(recruiter, admin, make_actor):
    csv = "name,email,phone,skills\nAnn Lee,ann@x.test,999,react;node\nBad Mail,not-an-email,,\n,noname@x.test,,\nAnn Dup,ann@x.test,,\nBo Ray,bo@x.test,,\n=cmd|x,evil@x.test,,\n"
    r = recruiter.post("/api/recruiter/candidates/import", files={"file": ("c.csv", csv.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["imported"] == 3 and j["failed"] == 3
    assert {e["row"] for e in j["errors"]} == {3, 4, 5}
    again = recruiter.post("/api/recruiter/candidates/import", files={"file": ("c.csv", csv.encode(), "text/csv")}).json()
    assert again["imported"] == 0
    assert recruiter.post("/api/recruiter/candidates/import", files={"file": ("c.txt", b"x", "text/plain")}).status_code == 400
    assert recruiter.post("/api/recruiter/candidates/import", files={"file": ("c.csv", b"foo,bar\n1,2", "text/csv")}).status_code == 422
    assert recruiter.post("/api/recruiter/candidates/import", files={"file": ("c.csv", b"", "text/csv")}).status_code == 400
    pool = recruiter.get("/api/recruiter/candidates/pool").json()
    assert pool["total"] == 3
    ids = [p["id"] for p in pool["items"]][:2]
    assert recruiter.post("/api/recruiter/candidates/pool/stage", json={"ids": ids, "stage": "interview"}).json()["updated"] == 2
    assert recruiter.get("/api/recruiter/candidates/pool", params={"stage": "interview"}).json()["total"] == 2
    assert recruiter.get("/api/recruiter/candidates/pool", params={"stage": "zzz"}).status_code == 422
    other = make_actor("recruiter")
    assert other.post("/api/recruiter/candidates/pool/stage", json={"ids": ids, "stage": "hired"}).json()["updated"] == 0
    exp = recruiter.get("/api/recruiter/candidates/pool/export").text
    assert "ann@x.test" in exp and "'=cmd|x" in exp  # formula injection neutralised
    m = recruiter.post("/api/recruiter/candidates/message", json={"pool_ids": ids, "subject": "Hello", "message": "We have a role"}).json()
    assert m["email"] == 2
    assert admin.get("/api/admin/emails").json()["total"] >= 2
    assert recruiter.post("/api/recruiter/candidates/message", json={"subject": "Hello", "message": "x1"}).status_code == 422
    assert recruiter.post("/api/recruiter/candidates/pool/delete", json={"ids": ids[:1]}).json()["deleted"] == 1


def test_bulk_application_status_stage_message_export(recruiter, admin, make_actor):
    job = _job(recruiter, admin)
    us = [make_actor("user") for _ in range(3)]
    apps = [_apply(u, job) for u in us]
    r = recruiter.post("/api/recruiter/applications/bulk", json={"ids": apps, "status": "shortlisted", "stage": "screening"}).json()
    assert r["updated"] == 3
    assert {a["status"] for a in recruiter.get("/api/recruiter/applications").json()["items"]} == {"shortlisted"}
    assert us[0].get("/api/notifications").json()["items"][0]["title"] == "Application update"
    other = make_actor("recruiter")
    assert other.post("/api/recruiter/applications/bulk", json={"ids": apps, "status": "rejected"}).json()["updated"] == 0
    assert recruiter.post("/api/recruiter/applications/bulk", json={"ids": apps}).status_code == 422
    assert recruiter.post("/api/recruiter/candidates/message", json={"application_ids": apps, "subject": "Update", "message": "Hello all"}).json()["in_app"] == 3
    exp = recruiter.get("/api/recruiter/applications/export", params={"status": "shortlisted"}).text
    assert exp.count("shortlisted") == 3 and "Stage" in exp
    assert recruiter.get(f"/api/recruiter/applications/{apps[0]}").status_code == 200  # static routes did not shadow the id route


def test_offer_lifecycle_both_sides(recruiter, admin, make_actor):
    job = _job(recruiter, admin)
    u = make_actor("user", "Cara Candidate")
    app_id = _apply(u, job)
    tpl = recruiter.post("/api/recruiter/offer-templates", json={"name": "Std", "body": "Hello {{candidate_name}}, join {{company}} as {{designation}} for {{salary}}."}).json()
    body = {"application_id": app_id, "designation": "Lead Dev", "salary": 1850000, "joining_date": iso(30), "template_id": tpl["id"]}
    o = recruiter.post("/api/recruiter/offers", json=body)
    assert o.status_code == 201, o.text
    o = o.json()
    assert o["status"] == "draft" and "Cara Candidate" in o["letter"] and "₹18,50,000" in o["letter"]
    assert recruiter.post("/api/recruiter/offers", json=body).status_code == 409
    assert u.get("/api/me/offers").json()["items"] == []  # drafts invisible
    assert u.post(f"/api/me/offers/{o['id']}/respond", json={"decision": "accept"}).status_code in (400, 404)
    assert recruiter.put(f"/api/recruiter/offers/{o['id']}", json={"salary": 2000000}).json()["salary"] == 2000000
    pdf = recruiter.get(f"/api/recruiter/offers/{o['id']}/pdf")
    assert pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    s = recruiter.post(f"/api/recruiter/offers/{o['id']}/send").json()
    assert s["status"] == "sent" and s["expires_at"]
    assert recruiter.put(f"/api/recruiter/offers/{o['id']}", json={"salary": 1}).status_code == 400
    assert recruiter.post(f"/api/recruiter/offers/{o['id']}/send").status_code == 400
    mine = u.get("/api/me/offers").json()["items"]
    assert len(mine) == 1 and mine[0]["designation"] == "Lead Dev"
    assert u.get(f"/api/me/offers/{o['id']}/pdf").content.startswith(b"%PDF")
    stranger = make_actor("user")
    assert stranger.get(f"/api/me/offers/{o['id']}").status_code == 404
    assert stranger.post(f"/api/me/offers/{o['id']}/respond", json={"decision": "accept"}).status_code == 404
    r = u.post(f"/api/me/offers/{o['id']}/respond", json={"decision": "accept", "note": "Thanks"})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert u.post(f"/api/me/offers/{o['id']}/respond", json={"decision": "decline"}).status_code == 400
    tracked = recruiter.get("/api/recruiter/offers").json()
    assert tracked["counts"] == {"accepted": 1}
    assert [h["status"] for h in recruiter.get(f"/api/recruiter/offers/{o['id']}").json()["history"]] == ["draft", "sent", "accepted"]
    other = make_actor("recruiter")
    assert other.get(f"/api/recruiter/offers/{o['id']}").status_code == 404
    assert other.post(f"/api/recruiter/offers/{o['id']}/withdraw").status_code == 404
    assert recruiter.post(f"/api/recruiter/offers/{o['id']}/withdraw").status_code == 400


def test_offer_decline_withdraw_and_expiry(recruiter, admin, make_actor):
    job = _job(recruiter, admin)
    u1, u2 = make_actor("user"), make_actor("user")
    a1, a2 = _apply(u1, job), _apply(u2, job)
    mk = lambda a: recruiter.post("/api/recruiter/offers", json={"application_id": a, "designation": "Dev", "salary": 100000}).json()
    o1, o2 = mk(a1), mk(a2)
    recruiter.post(f"/api/recruiter/offers/{o1['id']}/send")
    assert u1.post(f"/api/me/offers/{o1['id']}/respond", json={"decision": "decline"}).json()["status"] == "declined"
    recruiter.post(f"/api/recruiter/offers/{o2['id']}/send")
    assert recruiter.post(f"/api/recruiter/offers/{o2['id']}/withdraw").json()["status"] == "withdrawn"
    assert u2.post(f"/api/me/offers/{o2['id']}/respond", json={"decision": "accept"}).status_code == 400
    # expiry
    from app.database import offers_collection
    from bson import ObjectId

    o3 = recruiter.post("/api/recruiter/offers", json={"application_id": a1, "designation": "Dev", "salary": 5}).json()
    recruiter.post(f"/api/recruiter/offers/{o3['id']}/send")
    offers_collection.update_one({"_id": ObjectId(o3["id"])}, {"$set": {"expires_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)}})
    assert u1.post(f"/api/me/offers/{o3['id']}/respond", json={"decision": "accept"}).status_code == 400
    rejected = recruiter.patch(f"/api/recruiter/applications/{a2}/status", json={"status": "rejected"})
    assert recruiter.post("/api/recruiter/offers", json={"application_id": a2, "designation": "Dev", "salary": 5}).status_code == 400
