import pytest

from tests.conftest import JOB


ADMIN_GETS = [
    "/api/admin/stats", "/api/admin/users", "/api/admin/companies", "/api/admin/jobs", "/api/admin/applications",
    "/api/admin/videos", "/api/admin/reports", "/api/admin/skills", "/api/admin/banners", "/api/admin/settings",
    "/api/admin/interviews",
]


@pytest.mark.parametrize("url", ADMIN_GETS)
def test_admin_endpoints_are_admin_only(url, client, make_actor, admin):
    assert client.get(url).status_code == 401
    assert make_actor("user").get(url).status_code == 403
    assert make_actor("recruiter").get(url).status_code == 403
    assert admin.get(url).status_code == 200


def test_recruiter_endpoints_blocked_for_candidates(make_actor):
    u = make_actor("user")
    for url in ["/api/recruiter/dashboard", "/api/recruiter/jobs", "/api/recruiter/applications", "/api/recruiter/company"]:
        assert u.get(url).status_code == 403


def test_admin_stats_reflect_data(admin, live_job, make_actor):
    make_actor("user").post(f"/api/jobs/{live_job['id']}/apply", json={})
    s = admin.get("/api/admin/stats").json()
    assert s["users_by_role"]["admin"] == 1 and s["users_by_role"]["recruiter"] == 1 and s["users_by_role"]["user"] == 1
    assert s["jobs_total"] == 1 and s["jobs_approved"] == 1
    assert s["applications_total"] == 1 and s["applications_by_status"] == {"applied": 1}
    assert len(s["signups_last_14_days"]) == 14 and sum(x["count"] for x in s["applications_last_14_days"]) == 1


def test_admin_user_management(admin, make_actor):
    u = make_actor("user", "Zed Zebra")
    lst = admin.get("/api/admin/users?search=zebra").json()
    assert lst["total"] == 1 and "password_hash" not in str(lst)
    assert admin.get("/api/admin/users?role=admin").json()["total"] == 1
    # create + promote
    r = admin.post("/api/admin/users", json={"name": "New Rec", "email": "newrec@example.com", "password": "secret123", "role": "recruiter"})
    assert r.status_code == 201 and r.json()["role"] == "recruiter"
    assert admin.post("/api/admin/users", json={"name": "New Rec", "email": "newrec@example.com", "password": "secret123"}).status_code == 409
    assert admin.patch(f"/api/admin/users/{u.id}", json={"role": "recruiter"}).json()["role"] == "recruiter"
    # blocked filter
    admin.patch(f"/api/admin/users/{u.id}", json={"is_active": False})
    assert admin.get("/api/admin/users?is_active=false").json()["total"] == 1
    assert admin.get("/api/admin/users/not-an-id").status_code == 400


def test_admin_cannot_lock_themselves_out(admin):
    assert admin.patch(f"/api/admin/users/{admin.id}", json={"is_active": False}).status_code == 400
    assert admin.patch(f"/api/admin/users/{admin.id}", json={"role": "user"}).status_code == 400
    assert admin.delete(f"/api/admin/users/{admin.id}").status_code == 400


def test_blocking_recruiter_hides_their_jobs(admin, recruiter, live_job, client):
    assert client.get("/api/jobs").json()["total"] == 1
    admin.patch(f"/api/admin/users/{recruiter.id}", json={"is_active": False})
    assert client.get("/api/jobs").json()["total"] == 0


def test_delete_user_cascades(admin, recruiter, live_job, make_actor, client):
    user = make_actor("user")
    user.post(f"/api/jobs/{live_job['id']}/apply", json={})
    assert admin.delete(f"/api/admin/users/{user.id}").status_code == 200
    assert admin.get("/api/admin/applications").json()["total"] == 0
    assert admin.delete(f"/api/admin/users/{recruiter.id}").status_code == 200
    assert admin.get("/api/admin/jobs").json()["total"] == 0
    assert admin.get("/api/admin/companies").json()["total"] == 0
    assert client.post("/api/auth/login", json={"email": user.user["email"], "password": "secret123"}).status_code == 401


def test_company_moderation_and_verification(admin, make_actor):
    rec = make_actor("recruiter")
    c = rec.post("/api/recruiter/company", json={"name": "Globex"}).json()
    assert c["status"] == "pending"
    assert admin.get("/api/admin/companies?status=pending").json()["total"] == 1
    r = admin.patch(f"/api/admin/companies/{c['id']}", json={"status": "approved", "is_verified": True})
    assert r.json()["status"] == "approved" and r.json()["is_verified"] is True
    assert admin.patch(f"/api/admin/companies/{c['id']}", json={"status": "weird"}).status_code == 422
    assert any("approved" in n["title"].lower() for n in rec.get("/api/notifications").json()["items"])
    # rejected company cannot post jobs
    admin.patch(f"/api/admin/companies/{c['id']}", json={"status": "rejected"})
    assert rec.post("/api/recruiter/jobs", json=JOB).status_code == 403


def test_admin_posts_job_for_company_with_external_link(admin, recruiter, client, make_actor):
    c = recruiter.company
    r = admin.post("/api/admin/jobs", json={**JOB, "company_id": c["id"], "apply_url": "https://acme.test/apply"})
    assert r.status_code == 201 and r.json()["status"] == "approved"  # admin posts go live immediately
    assert admin.post("/api/admin/jobs", json=JOB).status_code == 422  # company_id required
    user = make_actor("user")
    res = user.post(f"/api/jobs/{r.json()['id']}/apply", json={})
    assert res.json()["redirect_url"] == "https://acme.test/apply"


def test_job_moderation_feature_reject_delete(admin, recruiter, client):
    j = recruiter.post("/api/recruiter/jobs", json=JOB).json()
    assert admin.get("/api/admin/jobs?status=pending").json()["total"] == 1
    r = admin.patch(f"/api/admin/jobs/{j['id']}/status", json={"status": "rejected", "reason": "Too vague"})
    assert r.json()["status"] == "rejected" and r.json()["rejection_reason"] == "Too vague"
    note = recruiter.get("/api/notifications").json()["items"][0]
    assert "Too vague" in note["message"]
    # editing a rejected job re-submits it for review
    assert recruiter.put(f"/api/recruiter/jobs/{j['id']}", json={"description": "A much clearer description now."}).json()["status"] == "pending"
    admin.patch(f"/api/admin/jobs/{j['id']}/status", json={"status": "approved"})
    assert admin.patch(f"/api/admin/jobs/{j['id']}/feature").json()["is_featured"] is True
    assert client.get("/api/jobs?featured=true").json()["total"] == 1
    assert admin.patch(f"/api/admin/jobs/{j['id']}/toggle").json()["is_active"] is False
    assert admin.delete(f"/api/admin/jobs/{j['id']}").status_code == 200
    assert admin.get(f"/api/admin/jobs/{j['id']}").status_code == 404


def test_auto_approve_setting(admin, recruiter):
    assert admin.put("/api/admin/settings", json={"auto_approve_jobs": True}).json()["auto_approve_jobs"] is True
    assert recruiter.post("/api/recruiter/jobs", json=JOB).json()["status"] == "approved"
    assert admin.put("/api/admin/settings", json={}).status_code == 400


def test_maintenance_mode_blocks_non_admins(admin, make_actor, client):
    u = make_actor("user")
    admin.put("/api/admin/settings", json={"maintenance_mode": True})
    assert client.post("/api/auth/login", json={"email": u.user["email"], "password": "secret123"}).status_code == 503
    assert client.post("/api/auth/login", json={"email": "admin@carevo.test", "password": "Admin@12345"}).status_code == 200


def test_admin_application_control(admin, live_job, make_actor):
    user = make_actor("user")
    app_id = user.post(f"/api/jobs/{live_job['id']}/apply", json={}).json()["application_id"]
    lst = admin.get("/api/admin/applications?search=" + user.user["name"].split()[0]).json()
    assert lst["total"] == 1 and lst["items"][0]["candidate"]["email"] == user.user["email"]
    assert admin.patch(f"/api/admin/applications/{app_id}/status", json={"status": "rejected"}).status_code == 200
    assert user.get(f"/api/applications/{app_id}").json()["status"] == "rejected"
    assert admin.delete(f"/api/admin/applications/{app_id}").status_code == 200
    assert user.get("/api/applications").json()["total"] == 0


def test_skills_and_categories_admin(admin, client):
    cats = client.get("/api/skills/categories").json()
    assert len(cats) >= 5
    assert len(client.get("/api/skills").json()) > 20  # seeded taxonomy
    c = admin.post("/api/admin/skill-categories", json={"name": "Blockchain"}).json()
    assert admin.post("/api/admin/skill-categories", json={"name": "blockchain"}).status_code == 409
    s = admin.post("/api/admin/skills", json={"name": "Solidity", "category_id": c["id"]})
    assert s.status_code == 201
    assert admin.post("/api/admin/skills", json={"name": "solidity"}).status_code == 409
    assert any(x["name"] == "Solidity" for x in client.get(f"/api/skills?category_id={c['id']}").json())
    assert admin.delete(f"/api/admin/skills/{s.json()['id']}").status_code == 200
    assert admin.delete(f"/api/admin/skill-categories/{c['id']}").status_code == 200


def test_user_skills(make_actor, client):
    u = make_actor("user")
    skill = client.get("/api/skills").json()[0]
    assert u.post("/api/skills/users/me/skills", json={"skill_id": skill["id"], "proficiency": "advanced"}).status_code == 201
    assert u.post("/api/skills/users/me/skills", json={"skill_id": skill["id"]}).status_code == 409
    mine = u.get("/api/skills/users/me/skills").json()
    assert mine[0]["name"] == skill["name"] and mine[0]["proficiency"] == "advanced"
    assert u.post("/api/skills/users/me/skills", json={"skill_id": skill["id"], "proficiency": "god"}).status_code == 422
    assert u.delete(f"/api/skills/users/me/skills/{skill['id']}").status_code == 200
    assert u.delete(f"/api/skills/users/me/skills/{skill['id']}").status_code == 404


def test_banners_and_broadcast(admin, make_actor, client):
    b = admin.post("/api/admin/banners", json={"title": "Hiring fest", "link_url": "https://example.com"}).json()
    assert len(client.get("/api/banners").json()) == 1
    admin.patch(f"/api/admin/banners/{b['id']}")
    assert client.get("/api/banners").json() == []
    assert admin.delete(f"/api/admin/banners/{b['id']}").status_code == 200

    u, r = make_actor("user"), make_actor("recruiter")
    out = admin.post("/api/admin/notifications", json={"title": "Maintenance", "message": "Sunday 2am", "audience": "recruiter"}).json()
    assert out["recipients"] == 1
    assert any(n["title"] == "Maintenance" for n in r.get("/api/notifications").json()["items"])
    assert not any(n["title"] == "Maintenance" for n in u.get("/api/notifications").json()["items"])


def test_public_stats(client, live_job, make_actor):
    make_actor("user")
    s = client.get("/api/public/stats").json()
    assert s == {"jobs": 1, "companies": 1, "candidates": 1}
