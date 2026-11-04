"""Job Reels: premium gating for Apply and recruiter profiles, video->job link."""
from tests.conftest import JOB


def reel(recruiter, admin, apply_url=None, **extra):
    job = {**JOB, **({"apply_url": apply_url} if apply_url else {})}
    j = recruiter.post("/api/recruiter/jobs", json=job).json()
    admin.patch(f"/api/admin/jobs/{j['id']}/status", json={"status": "approved"})
    v = recruiter.post("/api/videos", json={"title": "We are hiring!", "video_url": "https://cdn.test/v.mp4", "type": "job", "job_id": j["id"], **extra})
    assert v.status_code == 201, v.text
    return j, v.json()


def test_video_carries_job_summary_and_has_job_filter(recruiter, admin, client):
    j, v = reel(recruiter, admin, "https://careers.test/job/1")
    assert v["job"]["id"] == j["id"] and v["job"]["apply_type"] == "external" and v["job"]["is_open"]
    recruiter.post("/api/videos", json={"title": "Culture video", "video_url": "https://cdn.test/c.mp4", "type": "company"})
    all_items = client.get("/api/videos").json()["items"]
    with_job = client.get("/api/videos?has_job=true").json()["items"]
    assert len(all_items) == 2 and [x["id"] for x in with_job] == [v["id"]]


def test_cannot_link_someone_elses_job(recruiter, admin, make_actor):
    j, _ = reel(recruiter, admin)
    other = make_actor("recruiter")
    r = other.post("/api/videos", json={"title": "Sneaky link", "video_url": "https://cdn.test/v.mp4", "job_id": j["id"]})
    assert r.status_code == 422
    assert other.post("/api/videos", json={"title": "Sneaky link", "video_url": "https://cdn.test/v.mp4", "job_id": "nope"}).status_code == 400


def test_candidate_without_premium_cannot_apply_from_reel(recruiter, admin, make_actor):
    j, v = reel(recruiter, admin, "https://careers.test/job/1")
    user = make_actor("user")
    r = user.post(f"/api/videos/{v['id']}/apply")
    assert r.status_code == 402 and "Premium" in r.json()["detail"]
    assert user.get("/api/applications").json()["total"] == 0
    # the normal jobs page is NOT gated
    assert user.post(f"/api/jobs/{j['id']}/apply", json={}).status_code == 201


def test_premium_user_applies_from_reel_and_gets_the_company_link(recruiter, admin, make_actor):
    j, v = reel(recruiter, admin, "https://careers.test/job/1")
    user = make_actor("user")
    assert admin.patch(f"/api/admin/users/{user.id}/premium", json={"is_premium": True}).status_code == 200
    r = user.post(f"/api/videos/{v['id']}/apply")
    assert r.status_code == 201, r.text
    assert r.json()["redirect_url"] == "https://careers.test/job/1" and r.json()["source"] == "external"
    assert user.post(f"/api/videos/{v['id']}/apply").json()["already_applied"] is True
    assert user.get("/api/applications").json()["total"] == 1
    assert user.get("/api/auth/me").json()["is_premium"] is True


def test_gating_can_be_switched_off_by_admin(recruiter, admin, make_actor):
    _, v = reel(recruiter, admin, "https://careers.test/job/1")
    user = make_actor("user")
    assert admin.put("/api/admin/settings", json={"premium_gating": False}).status_code == 200
    assert user.post(f"/api/videos/{v['id']}/apply").status_code == 201


def test_recruiter_profile_needs_premium_for_candidates_only(recruiter, admin, make_actor):
    reel(recruiter, admin)
    user, other_rec = make_actor("user"), make_actor("recruiter")
    r = user.get(f"/api/recruiters/{recruiter.id}")
    assert r.status_code == 402
    assert other_rec.get(f"/api/recruiters/{recruiter.id}").status_code == 200
    assert admin.get(f"/api/recruiters/{recruiter.id}").status_code == 200
    admin.patch(f"/api/admin/users/{user.id}/premium", json={"is_premium": True})
    body = user.get(f"/api/recruiters/{recruiter.id}").json()
    assert body["name"] == recruiter.user["name"] and body["company"]["name"] == "Acme Corp" and len(body["jobs"]) == 1
    assert "email" not in body and "phone" not in body  # contact details are never exposed
    assert user.get(f"/api/recruiters/{user.id}").status_code == 404  # only recruiters


def test_premium_expiry_and_revoke(admin, make_actor):
    from datetime import timedelta

    from app.database import users_collection
    from app.utils import now

    user = make_actor("user")
    admin.patch(f"/api/admin/users/{user.id}/premium", json={"is_premium": True, "days": 30})
    assert user.get("/api/users/me/premium").json()["is_premium"] is True
    from bson import ObjectId

    users_collection.update_one({"_id": ObjectId(user.id)}, {"$set": {"premium_until": now() - timedelta(days=1)}})
    assert user.get("/api/users/me/premium").json()["is_premium"] is False
    admin.patch(f"/api/admin/users/{user.id}/premium", json={"is_premium": True})
    admin.patch(f"/api/admin/users/{user.id}/premium", json={"is_premium": False})
    assert user.get("/api/users/me/premium").json()["is_premium"] is False


def test_premium_request_flow(admin, make_actor):
    user = make_actor("user")
    assert user.post("/api/users/me/premium/request").status_code == 201
    again = user.post("/api/users/me/premium/request").json()
    assert again["already_requested"] is True
    reqs = admin.get("/api/admin/premium-requests").json()
    assert reqs["total"] == 1 and reqs["items"][0]["user"]["email"] == user.user["email"]
    assert any("Premium request" in n["title"] for n in admin.get("/api/notifications").json()["items"])
    admin.patch(f"/api/admin/users/{user.id}/premium", json={"is_premium": True, "days": 30})
    assert admin.get("/api/admin/premium-requests").json()["total"] == 0
    assert user.post("/api/users/me/premium/request").status_code == 400  # already premium


def test_nobody_can_grant_themselves_premium(make_actor, admin):
    user = make_actor("user")
    assert user.patch(f"/api/admin/users/{user.id}/premium", json={"is_premium": True}).status_code == 403
    user.put("/api/users/me", json={"name": "Hacker", "is_premium": True, "role": "admin"})
    me = user.get("/api/auth/me").json()
    assert me["is_premium"] is False and me["role"] == "user"
    rec = make_actor("recruiter")
    assert admin.patch(f"/api/admin/users/{rec.id}/premium", json={"is_premium": True}).status_code == 400
