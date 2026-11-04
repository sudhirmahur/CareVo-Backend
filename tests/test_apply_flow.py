"""The core business flow: recruiter posts job with the company's link -> user applies ->
user is sent to the link AND the application shows up in their history."""
from tests.conftest import JOB, make_docx


def test_recruiter_job_needs_admin_approval_then_goes_live(recruiter, admin, make_actor, client):
    r = recruiter.post("/api/recruiter/jobs", json={**JOB, "apply_url": "https://careers.acme.test/x"})
    assert r.status_code == 201
    job = r.json()
    assert job["status"] == "pending" and job["apply_type"] == "external"

    user = make_actor("user")
    # not visible to candidates yet
    assert client.get("/api/jobs").json()["total"] == 0
    assert user.get(f"/api/jobs/{job['id']}").status_code == 404
    assert user.post(f"/api/jobs/{job['id']}/apply", json={}).status_code == 404

    assert admin.patch(f"/api/admin/jobs/{job['id']}/status", json={"status": "approved"}).status_code == 200
    listing = client.get("/api/jobs").json()
    assert listing["total"] == 1 and listing["items"][0]["company_name"] == "Acme Corp"
    # recruiter is notified of approval
    assert any(n["title"] == "Job approved" for n in recruiter.get("/api/notifications").json()["items"])


def test_external_apply_records_history_and_returns_redirect(make_actor, live_job, recruiter):
    user = make_actor("user")
    url = "https://careers.acme.test/jobs/react-dev"

    r = user.post(f"/api/jobs/{live_job['id']}/apply", json={})  # no resume needed for external link
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["redirect_url"] == url
    assert body["already_applied"] is False and body["source"] == "external"

    # -> shows up in application history
    hist = user.get("/api/applications").json()
    assert hist["total"] == 1
    item = hist["items"][0]
    assert item["status"] == "applied"
    assert item["job"]["title"] == "Senior React Developer"
    assert item["company_name"] == "Acme Corp"
    assert item["job"]["apply_url"] == url and item["source"] == "external"
    assert item["status_history"][0]["status"] == "applied"

    # job card now reflects it
    job = user.get(f"/api/jobs/{live_job['id']}").json()
    assert job["has_applied"] is True and job["application_status"] == "applied"

    # clicking Apply again: no duplicate, but the link is returned again
    again = user.post(f"/api/jobs/{live_job['id']}/apply", json={})
    assert again.status_code == 201 and again.json()["already_applied"] is True
    assert again.json()["redirect_url"] == url
    assert user.get("/api/applications").json()["total"] == 1

    # recruiter sees the candidate + gets notified
    apps = recruiter.get("/api/recruiter/applications").json()
    assert apps["total"] == 1 and apps["items"][0]["candidate"]["email"] == user.user["email"]
    assert any(n["title"] == "New application" for n in recruiter.get("/api/notifications").json()["items"])
    assert recruiter.get(f"/api/recruiter/jobs/{live_job['id']}").json()["applications_count"] == 1


def test_internal_apply_requires_resume(make_actor, recruiter, admin):
    j = recruiter.post("/api/recruiter/jobs", json=JOB).json()  # no apply_url
    admin.patch(f"/api/admin/jobs/{j['id']}/status", json={"status": "approved"})
    user = make_actor("user")
    r = user.post(f"/api/jobs/{j['id']}/apply", json={})
    assert r.status_code == 400 and "resume" in r.json()["detail"].lower()

    up = user.post("/api/resumes", files={"file": ("cv.docx", make_docx("Jane\nExperience\nEducation"), "application/octet-stream")})
    assert up.status_code == 201, up.text
    r = user.post(f"/api/jobs/{j['id']}/apply", json={"resume_id": up.json()["resume_id"], "cover_letter": "Hire me"})
    assert r.status_code == 201 and r.json()["redirect_url"] is None and r.json()["source"] == "internal"
    assert user.get("/api/applications").json()["items"][0]["cover_letter"] == "Hire me"


def test_cannot_use_someone_elses_resume(make_actor, live_job):
    a, b = make_actor("user"), make_actor("user")
    rid = a.post("/api/resumes", files={"file": ("cv.docx", make_docx("x"), "application/octet-stream")}).json()["resume_id"]
    assert b.post(f"/api/jobs/{live_job['id']}/apply", json={"resume_id": rid}).status_code == 404


def test_only_candidates_can_apply(recruiter, admin, live_job):
    assert recruiter.post(f"/api/jobs/{live_job['id']}/apply", json={}).status_code == 403
    assert admin.post(f"/api/jobs/{live_job['id']}/apply", json={}).status_code == 403


def test_recruiter_response_reaches_candidate(make_actor, live_job, recruiter):
    user = make_actor("user")
    app_id = user.post(f"/api/jobs/{live_job['id']}/apply", json={}).json()["application_id"]

    r = recruiter.patch(f"/api/recruiter/applications/{app_id}/status", json={"status": "shortlisted", "note": "Great profile"})
    assert r.status_code == 200
    item = user.get(f"/api/applications/{app_id}").json()
    assert item["status"] == "shortlisted"
    assert [h["status"] for h in item["status_history"]] == ["applied", "shortlisted"]
    notes = user.get("/api/notifications").json()
    assert notes["unread_count"] >= 1 and "shortlisted" in notes["items"][0]["message"]

    # interview
    iv = recruiter.post(
        "/api/recruiter/interviews",
        json={"application_id": app_id, "scheduled_at": "2099-01-01T10:00:00Z", "mode": "online", "meeting_link": "https://meet.test/abc"},
    )
    assert iv.status_code == 201, iv.text
    mine = user.get("/api/interviews").json()
    assert mine["total"] == 1 and mine["items"][0]["job_title"] == "Senior React Developer"
    assert user.get(f"/api/applications/{app_id}").json()["interviews"][0]["meeting_link"] == "https://meet.test/abc"
    # online interview needs a link
    bad = recruiter.post("/api/recruiter/interviews", json={"application_id": app_id, "scheduled_at": "2099-01-01T10:00:00Z", "mode": "online"})
    assert bad.status_code == 422

    recruiter.patch(f"/api/recruiter/applications/{app_id}/status", json={"status": "accepted"})
    assert user.get("/api/applications?status=accepted").json()["total"] == 1


def test_recruiters_cannot_touch_other_recruiters_data(make_actor, live_job, admin):
    user = make_actor("user")
    app_id = user.post(f"/api/jobs/{live_job['id']}/apply", json={}).json()["application_id"]
    other = make_actor("recruiter")
    assert other.get("/api/recruiter/applications").json()["total"] == 0
    assert other.get(f"/api/recruiter/applications/{app_id}").status_code == 404
    assert other.patch(f"/api/recruiter/applications/{app_id}/status", json={"status": "rejected"}).status_code == 404
    assert other.put(f"/api/recruiter/jobs/{live_job['id']}", json={"title": "Hacked title"}).status_code == 404
    assert other.delete(f"/api/recruiter/jobs/{live_job['id']}").status_code == 404
    # a candidate can't see other candidates' applications
    other_user = make_actor("user")
    assert other_user.get(f"/api/applications/{app_id}").status_code == 404
    assert other_user.get("/api/applications").json()["total"] == 0


def test_withdraw(make_actor, live_job):
    user = make_actor("user")
    app_id = user.post(f"/api/jobs/{live_job['id']}/apply", json={}).json()["application_id"]
    assert user.post(f"/api/applications/{app_id}/withdraw").status_code == 200
    assert user.get(f"/api/applications/{app_id}").json()["status"] == "withdrawn"
    assert user.post(f"/api/applications/{app_id}/withdraw").status_code == 400


def test_history_survives_job_deletion(make_actor, live_job, recruiter):
    user = make_actor("user")
    user.post(f"/api/jobs/{live_job['id']}/apply", json={})
    assert recruiter.delete(f"/api/recruiter/jobs/{live_job['id']}").status_code == 200
    item = user.get("/api/applications").json()["items"][0]
    assert item["job"]["title"] == "Senior React Developer" and item["job"]["status"] == "removed"


def test_expired_job_blocks_apply_and_listing(recruiter, admin, make_actor, client):
    j = recruiter.post("/api/recruiter/jobs", json={**JOB, "application_deadline": "2020-01-01T00:00:00Z"}).json()
    admin.patch(f"/api/admin/jobs/{j['id']}/status", json={"status": "approved"})
    user = make_actor("user")
    assert client.get("/api/jobs").json()["total"] == 0
    assert user.post(f"/api/jobs/{j['id']}/apply", json={}).status_code == 400


def test_job_validation(recruiter):
    assert recruiter.post("/api/recruiter/jobs", json={**JOB, "apply_url": "ftp://bad"}).status_code == 422
    assert recruiter.post("/api/recruiter/jobs", json={**JOB, "apply_url": "not a url"}).status_code == 422
    assert recruiter.post("/api/recruiter/jobs", json={**JOB, "salary_min": 10, "salary_max": 5}).status_code == 422
    assert recruiter.post("/api/recruiter/jobs", json={**JOB, "title": "ab"}).status_code == 422
    assert recruiter.post("/api/recruiter/jobs", json={**JOB, "job_type": "weekend"}).status_code == 422


def test_recruiter_needs_company_first(make_actor):
    rec = make_actor("recruiter")
    r = rec.post("/api/recruiter/jobs", json=JOB)
    assert r.status_code == 400


def test_search_and_filters(client, recruiter, admin):
    def post(**kw):
        j = recruiter.post("/api/recruiter/jobs", json={**JOB, **kw}).json()
        admin.patch(f"/api/admin/jobs/{j['id']}/status", json={"status": "approved"})
        return j

    post(title="Python Backend Engineer", skills=["Python", "FastAPI"], location="Bangalore", job_type="contract", salary_max=2000000)
    post(title="UI Designer", skills=["Figma"], location="Delhi", job_type="internship", salary_min=10000, salary_max=20000)
    post(title="React Developer", skills=["React"], location="Remote")

    def total(q):
        return client.get(f"/api/jobs?{q}").json()["total"]

    assert total("") == 3
    assert total("search=python") == 1
    assert total("search=figma") == 1
    assert total("location=delhi") == 1
    assert total("job_type=contract") == 1
    assert total("skills=Python,Figma") == 2
    assert total("min_salary=1900000") == 1
    assert total("company=acme") == 3 and total("company=nothere") == 0
    assert client.get("/api/jobs?job_type=bad").status_code == 422
    assert total("search=.*") == 0  # regex characters are escaped, not executed
    page = client.get("/api/jobs?limit=2&page=2").json()
    assert len(page["items"]) == 1 and page["pages"] == 2


def test_save_unsave_and_recommendations(make_actor, live_job, client):
    user = make_actor("user")
    user.put("/api/users/me/profile", json={"skills": ["react"]})
    assert user.post(f"/api/jobs/{live_job['id']}/save").status_code == 201
    assert user.post(f"/api/jobs/{live_job['id']}/save").status_code == 201  # idempotent
    saved = user.get("/api/jobs/saved").json()
    assert saved["total"] == 1 and saved["items"][0]["is_saved"] is True
    rec = user.get("/api/jobs/recommended").json()
    assert rec["items"][0]["match_score"] == 1
    assert user.delete(f"/api/jobs/{live_job['id']}/save").status_code == 200
    assert user.get("/api/jobs/saved").json()["total"] == 0


def test_recruiter_dashboard(live_job, recruiter, make_actor):
    make_actor("user").post(f"/api/jobs/{live_job['id']}/apply", json={})
    d = recruiter.get("/api/recruiter/dashboard").json()
    assert d["jobs_total"] == 1 and d["jobs_active"] == 1
    assert d["applications_total"] == 1 and d["applications_by_status"]["applied"] == 1
    assert sum(x["count"] for x in d["applications_last_7_days"]) == 1


def test_recruiter_toggle_and_update(live_job, recruiter, client):
    r = recruiter.patch(f"/api/recruiter/jobs/{live_job['id']}/toggle")
    assert r.json()["is_active"] is False
    assert client.get("/api/jobs").json()["total"] == 0
    recruiter.patch(f"/api/recruiter/jobs/{live_job['id']}/toggle")
    r = recruiter.put(f"/api/recruiter/jobs/{live_job['id']}", json={"title": "Staff React Engineer", "clear_apply_url": True})
    assert r.status_code == 200 and r.json()["title"] == "Staff React Engineer" and r.json()["apply_type"] == "internal"
