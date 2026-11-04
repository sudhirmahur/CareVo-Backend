"""Media uploads: Cloudinary (signed REST) and local fallback, content validation, permissions."""
from tests.conftest import JPEG, MP3, MP4, PNG, WEBM


def up(actor, name, data, purpose="avatar", mime="application/octet-stream"):
    return actor.post("/api/media/upload", files={"file": (name, data, mime)}, data={"purpose": purpose})


def test_local_fallback_image_upload_and_serving(make_actor, client):
    u = make_actor("user")
    r = up(u, "me.jpg", JPEG)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "local" and body["kind"] == "image" and body["url"].startswith("/uploads/media/image/")
    served = client.get(body["url"])
    assert served.status_code == 200
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in served.headers["content-security-policy"]


def test_cloudinary_signed_upload_for_image_video_and_audio(make_actor, fake_cloudinary):
    rec = make_actor("recruiter")
    img = up(rec, "logo.png", PNG, "logo")
    vid = up(rec, "tour.mp4", MP4, "video")
    aud = up(rec, "voice.mp3", MP3, "chat")
    for r in (img, vid, aud):
        assert r.status_code == 201, r.text
        assert r.json()["provider"] == "cloudinary" and r.json()["url"].startswith("https://res.cloudinary.com/test-cloud/")
    assert all(c["ok"] for c in fake_cloudinary), "signature must be valid"
    paths = [c["path"] for c in fake_cloudinary]
    assert "/v1_1/test-cloud/image/upload" in paths
    # Cloudinary stores audio and video under resource type "video"
    assert paths.count("/v1_1/test-cloud/video/upload") == 2
    assert fake_cloudinary[0]["fields"]["folder"] == "carevo/logo"


def test_cloudinary_error_is_reported_cleanly(make_actor, fake_cloudinary, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "CLOUDINARY_API_SECRET", "wrong-secret")
    u = make_actor("user")
    r = up(u, "a.jpg", JPEG)
    assert r.status_code == 502 and "Invalid Signature" in r.json()["detail"]


def test_cloudinary_unreachable(make_actor, monkeypatch):
    from app import config

    for k, v in (("CLOUDINARY_CLOUD_NAME", "c"), ("CLOUDINARY_API_KEY", "k"), ("CLOUDINARY_API_SECRET", "s"), ("CLOUDINARY_API_BASE", "http://127.0.0.1:9/v1_1")):
        monkeypatch.setattr(config, k, v)
    r = up(make_actor("user"), "a.jpg", JPEG)
    assert r.status_code == 502 and "unreachable" in r.json()["detail"]


def test_content_not_extension_decides_the_type(make_actor):
    u = make_actor("user")
    assert up(u, "script.jpg", b"<script>alert(1)</script>").status_code == 400
    assert up(u, "x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>').status_code == 400
    assert up(u, "page.html", b"<html></html>").status_code == 400
    assert up(u, "evil.exe", b"MZ\x90\x00").status_code == 400
    assert up(u, "empty.png", b"").status_code == 400
    assert up(u, "real.png.exe", PNG).status_code == 201  # real PNG bytes are fine whatever the name


def test_wrong_kind_for_purpose_and_size_limits(make_actor, monkeypatch):
    from app.services import storage

    rec = make_actor("recruiter")
    assert up(rec, "a.mp4", MP4, "thumbnail").status_code == 400  # thumbnail must be an image
    assert up(rec, "a.jpg", JPEG, "video").status_code == 400  # video must be a video
    assert up(make_actor("user"), "a.mp4", MP4, "chat").status_code == 201  # chat allows video
    monkeypatch.setitem(storage.LIMITS_MB, "image", 0)
    assert up(rec, "big.jpg", JPEG, "logo").status_code == 400


def test_purpose_permissions(make_actor, admin):
    user, rec = make_actor("user"), make_actor("recruiter")
    assert up(user, "a.jpg", JPEG, "avatar").status_code == 201
    assert up(user, "a.jpg", JPEG, "logo").status_code == 403
    assert up(user, "a.mp4", MP4, "video").status_code == 403
    assert up(rec, "a.jpg", JPEG, "landing").status_code == 403
    assert up(rec, "a.jpg", JPEG, "banner").status_code == 403
    assert up(admin, "a.jpg", JPEG, "landing").status_code == 201
    assert up(user, "a.jpg", JPEG, "nonsense").status_code == 422


def test_upload_requires_login(client):
    r = client.post("/api/media/upload", files={"file": ("a.jpg", JPEG, "image/jpeg")}, data={"purpose": "avatar"})
    assert r.status_code == 401


def test_hourly_upload_quota(make_actor, monkeypatch):
    from app.routes import media

    monkeypatch.setattr(media, "UPLOADS_PER_HOUR", 2)
    u = make_actor("user")
    assert up(u, "a.jpg", JPEG).status_code == 201
    assert up(u, "b.jpg", JPEG).status_code == 201
    assert up(u, "c.jpg", JPEG).status_code == 429


def test_video_delete_removes_cloudinary_asset(make_actor, fake_cloudinary):
    rec = make_actor("recruiter")
    url = up(rec, "tour.mp4", MP4, "video").json()["url"]
    v = rec.post("/api/videos", json={"title": "Office tour", "video_url": url, "type": "company"})
    assert v.status_code == 201, v.text
    assert rec.delete(f"/api/videos/{v.json()['id']}").status_code == 200
    destroys = [c for c in fake_cloudinary if c["path"].endswith("/destroy")]
    assert len(destroys) == 1 and destroys[0]["ok"] and destroys[0]["fields"]["public_id"] == "carevo/video/abc123"


def test_profile_image_accepts_uploaded_file_but_not_javascript_urls(make_actor):
    u = make_actor("user")
    url = up(u, "me.jpg", JPEG).json()["url"]
    assert u.put("/api/users/me", json={"profile_image": url}).status_code == 200
    assert u.put("/api/users/me", json={"profile_image": "javascript:alert(1)"}).status_code == 422
    assert u.put("/api/users/me", json={"profile_image": "/uploads/../../etc/passwd"}).status_code == 422


def test_chat_attachments_image_audio_video(make_actor):
    a, b = make_actor("user"), make_actor("recruiter")
    for name, data, kind in (("p.jpg", JPEG, "image"), ("v.mp3", MP3, "audio"), ("c.mp4", MP4, "video")):
        up_ = up(a, name, data, "chat").json()
        r = a.post("/api/messages", json={"receiver_id": b.id, "attachment": {"url": up_["url"], "kind": kind, "name": name}})
        assert r.status_code == 201, r.text
    thread = b.get(f"/api/messages/with/{a.id}").json()["items"]
    assert [m["attachment"]["kind"] for m in thread] == ["image", "audio", "video"]
    convo = b.get("/api/messages/conversations").json()["items"][0]
    assert "Attachment" in convo["last_message"]
    # empty message without attachment, and hostile attachment URLs are refused
    assert a.post("/api/messages", json={"receiver_id": b.id, "message": "  "}).status_code == 422
    for bad in ("javascript:alert(1)", "http://evil.test/x.jpg", "//evil.test/x", "/uploads/media/../../secret"):
        assert a.post("/api/messages", json={"receiver_id": b.id, "attachment": {"url": bad, "kind": "image"}}).status_code == 422
