import pytest

VIDEO = {"title": "Life at Acme", "video_url": "https://cdn.test/acme.mp4", "type": "company", "tags": ["culture", "Culture"]}


def test_only_recruiters_publish_videos(make_actor, recruiter):
    assert make_actor("user").post("/api/videos", json=VIDEO).status_code == 403
    r = recruiter.post("/api/videos", json=VIDEO)
    assert r.status_code == 201
    assert r.json()["tags"] == ["culture"] and r.json()["company_name"] == "Acme Corp"
    assert recruiter.post("/api/videos", json={**VIDEO, "video_url": "javascript:alert(1)"}).status_code == 422


def test_video_engagement(recruiter, make_actor, client):
    v = recruiter.post("/api/videos", json=VIDEO).json()
    user = make_actor("user")
    feed = user.get("/api/videos").json()
    assert feed["total"] == 1 and feed["items"][0]["is_liked"] is False

    assert user.post(f"/api/videos/{v['id']}/like").json() == {"is_liked": True, "likes_count": 1}
    assert user.post(f"/api/videos/{v['id']}/like").json()["likes_count"] == 1  # idempotent
    assert user.post(f"/api/videos/{v['id']}/save").status_code == 201
    c = user.post(f"/api/videos/{v['id']}/comments", json={"comment": "Nice!"})
    assert c.status_code == 201 and c.json()["author"]["name"] == user.user["name"]
    assert user.post(f"/api/videos/{v['id']}/comments", json={"comment": "   "}).status_code == 422

    got = user.get(f"/api/videos/{v['id']}").json()
    assert got["likes_count"] == 1 and got["comments_count"] == 1 and got["is_liked"] and got["is_saved"] and got["views_count"] == 1
    assert user.get("/api/videos/saved").json()["total"] == 1
    # recruiter got notified
    titles = [n["title"] for n in recruiter.get("/api/notifications").json()["items"]]
    assert "Someone liked your video" in titles and "New comment" in titles
    # public (logged-out) feed works too
    assert client.get("/api/videos").json()["items"][0]["likes_count"] == 1

    other = make_actor("user")
    assert other.delete(f"/api/videos/comments/{c.json()['id']}").status_code == 403
    assert user.delete(f"/api/videos/comments/{c.json()['id']}").status_code == 200  # author
    assert user.get(f"/api/videos/{v['id']}").json()["comments_count"] == 0
    assert user.delete(f"/api/videos/{v['id']}/like").json() == {"is_liked": False, "likes_count": 0}
    assert user.delete(f"/api/videos/{v['id']}/save").json()["is_saved"] is False


def test_video_ownership(recruiter, make_actor, admin):
    v = recruiter.post("/api/videos", json=VIDEO).json()
    other = make_actor("recruiter")
    assert other.delete(f"/api/videos/{v['id']}").status_code == 403
    assert other.put(f"/api/videos/{v['id']}", json=VIDEO).status_code == 403
    assert recruiter.put(f"/api/videos/{v['id']}", json={**VIDEO, "title": "Renamed video"}).json()["title"] == "Renamed video"
    assert admin.delete(f"/api/videos/{v['id']}").status_code == 200  # admin can remove anything


def test_admin_video_moderation(recruiter, admin, client):
    v = recruiter.post("/api/videos", json=VIDEO).json()
    admin.patch(f"/api/admin/videos/{v['id']}/moderate", json={"is_removed": True})
    assert client.get("/api/videos").json()["total"] == 0
    assert client.get(f"/api/videos/{v['id']}").status_code == 404
    assert admin.get("/api/admin/videos").json()["total"] == 1
    admin.patch(f"/api/admin/videos/{v['id']}/moderate", json={"is_removed": False})
    assert client.get("/api/videos").json()["total"] == 1


def test_video_upload_validation(recruiter, make_actor):
    assert recruiter.post("/api/videos/upload", files={"file": ("a.exe", b"MZ", "application/octet-stream")}).status_code == 400
    ok = recruiter.post("/api/videos/upload", files={"file": ("a.webm", b"\x1a\x45\xdf\xa3rest", "video/webm")})
    assert ok.status_code == 201 and ok.json()["video_url"].startswith("/uploads/media/video/")


def test_follow_company(make_actor, recruiter, client):
    user = make_actor("user")
    cid = recruiter.company["id"]
    assert user.post("/api/follow", json={"following_id": cid, "type": "company"}).status_code == 201
    assert user.post("/api/follow", json={"following_id": cid, "type": "company"}).status_code == 201  # idempotent
    assert user.get(f"/api/companies/{cid}").json()["is_following"] is True
    assert user.get(f"/api/companies/{cid}").json()["followers_count"] == 1
    assert recruiter.get("/api/recruiter/followers").json()["total"] == 1
    assert user.get("/api/follow/following").json()["items"][0]["name"] == "Acme Corp"
    assert any(n["title"] == "New follower" for n in recruiter.get("/api/notifications").json()["items"])
    assert recruiter.post("/api/follow", json={"following_id": cid, "type": "company"}).status_code == 400  # own company
    assert user.delete(f"/api/follow/{cid}").json()["is_following"] is False
    assert recruiter.get("/api/recruiter/dashboard").json()["followers"] == 0


def test_company_directory(client, recruiter, make_actor):
    assert client.get("/api/companies").json()["total"] == 1
    assert client.get("/api/companies?search=zzz").json()["total"] == 0
    pending = make_actor("recruiter").post("/api/recruiter/company", json={"name": "Hidden Inc"}).json()
    assert client.get(f"/api/companies/{pending['id']}").status_code == 404  # unapproved companies are not public
    assert client.get("/api/companies").json()["total"] == 1


def test_messages(make_actor):
    a, b = make_actor("user"), make_actor("recruiter")
    assert a.post("/api/messages", json={"receiver_id": a.id, "message": "hi me"}).status_code == 400
    assert a.post("/api/messages", json={"receiver_id": "x", "message": "hi"}).status_code == 400
    assert a.post("/api/messages", json={"receiver_id": b.id, "message": "  "}).status_code == 422
    assert a.post("/api/messages", json={"receiver_id": b.id, "message": "Hello, is the role open?"}).status_code == 201
    a.post("/api/messages", json={"receiver_id": b.id, "message": "Second"})

    convs = b.get("/api/messages/conversations").json()
    assert convs["unread_total"] == 2 and convs["items"][0]["peer"]["id"] == a.id
    assert b.get("/api/messages/unread-count").json()["count"] == 2
    thread = b.get(f"/api/messages/with/{a.id}").json()["items"]
    assert [m["message"] for m in thread] == ["Hello, is the role open?", "Second"]
    assert b.get("/api/messages/unread-count").json()["count"] == 0  # reading marks as read
    b.post("/api/messages", json={"receiver_id": a.id, "message": "Yes!"})
    mine = a.get("/api/messages/conversations").json()["items"][0]
    assert mine["last_message"] == "Yes!" and mine["unread"] == 1 and mine["last_from_me"] is False
    assert a.get(f"/api/messages/peer/{b.id}").json()["role"] == "recruiter"
    # strangers don't see each other's threads
    c = make_actor("user")
    assert c.get(f"/api/messages/with/{a.id}").json()["items"] == []


def test_notifications_lifecycle(make_actor, admin):
    u = make_actor("user")
    admin.post("/api/admin/notifications", json={"title": "One", "message": "first msg"})
    admin.post("/api/admin/notifications", json={"title": "Two", "message": "second msg"})
    lst = u.get("/api/notifications").json()
    assert lst["total"] == 2 and lst["unread_count"] == 2 and lst["items"][0]["title"] == "Two"
    assert u.get("/api/notifications/unread-count").json()["count"] == 2
    assert u.patch(f"/api/notifications/{lst['items'][0]['id']}/read").status_code == 200
    assert u.get("/api/notifications?unread=true").json()["total"] == 1
    assert u.post("/api/notifications/read-all").json()["updated"] == 1
    assert u.delete(f"/api/notifications/{lst['items'][1]['id']}").status_code == 200
    assert u.get("/api/notifications").json()["total"] == 1
    other = make_actor("user")
    assert other.patch(f"/api/notifications/{lst['items'][0]['id']}/read").status_code == 404


def test_report_flow(make_actor, recruiter, live_job, admin):
    user = make_actor("user")
    assert user.post("/api/reports", json={"content_type": "job", "content_id": live_job["id"], "reason": "Looks like a scam"}).status_code == 201
    assert user.post("/api/reports", json={"content_type": "job", "content_id": live_job["id"], "reason": "again"}).status_code == 409
    assert user.post("/api/reports", json={"content_type": "job", "content_id": "0" * 24, "reason": "ghost"}).status_code == 404
    assert any(n["title"] == "New report" for n in admin.get("/api/notifications").json()["items"])
    rep = admin.get("/api/admin/reports?status=pending").json()
    assert rep["total"] == 1 and rep["items"][0]["target_label"] == "Senior React Developer"
    rid = rep["items"][0]["id"]
    assert admin.patch(f"/api/admin/reports/{rid}", json={"action": "remove_content"}).status_code == 200
    assert admin.get(f"/api/admin/jobs/{live_job['id']}").status_code == 404  # content removed
    assert admin.get("/api/admin/reports?status=resolved").json()["total"] == 1


def test_report_user_and_block(make_actor, admin):
    bad, victim = make_actor("user"), make_actor("user")
    victim.post("/api/reports", json={"content_type": "user", "content_id": bad.id, "reason": "Harassing me"})
    rid = admin.get("/api/admin/reports").json()["items"][0]["id"]
    assert admin.patch(f"/api/admin/reports/{rid}", json={"action": "remove_content"}).status_code == 400
    assert admin.patch(f"/api/admin/reports/{rid}", json={"action": "block_user"}).status_code == 200
    assert bad.get("/api/auth/me").status_code == 403
