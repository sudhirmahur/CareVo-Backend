"""Security hardening + share/SEO + direct upload: encrypted 2FA seeds, extra rate limits, new-device alerts,
admin audit trail, robots/sitemap/share pages, signed direct-to-Cloudinary video upload, optional error monitoring."""
import hashlib
import sys

import pytest

from app import config
from app.database import audit_logs_collection, email_outbox_collection, users_collection
from app.services import secretbox, totp
from tests.conftest import JOB


# ------------------------------------------------------------------ encrypted secrets
def test_secretbox_roundtrip_legacy_and_tamper():
    sealed = secretbox.seal("JBSWY3DPEHPK3PXP")
    assert sealed.startswith("enc:v1:") and "JBSWY3DPEHPK3PXP" not in sealed
    assert secretbox.reveal(sealed) == "JBSWY3DPEHPK3PXP"
    assert secretbox.seal(sealed) == sealed, "already sealed values are not sealed twice"
    assert secretbox.reveal("PLAINTEXTLEGACY") == "PLAINTEXTLEGACY", "values stored before encryption still work"
    assert secretbox.reveal(sealed[:-4] + "AAAA") == "", "tampered ciphertext fails closed"
    assert secretbox.reveal("") == "" and secretbox.reveal(None) == ""


def test_secretbox_uses_the_dedicated_key_when_set(monkeypatch):
    a = secretbox.seal("SECRETSEED")
    monkeypatch.setattr(config, "DATA_ENCRYPTION_KEY", "a-completely-different-key-for-data")
    assert secretbox.reveal(a) == "", "another key cannot read it"
    b = secretbox.seal("SECRETSEED")
    assert secretbox.reveal(b) == "SECRETSEED"


def test_totp_seed_is_stored_encrypted_and_login_still_works(make_actor, client):
    u = make_actor("user")
    setup = u.post("/api/auth/2fa/totp/setup").json()
    raw = users_collection.find_one({"email": u.user["email"]})
    assert raw["twofa"]["pending_secret"].startswith("enc:v1:") and setup["secret"] not in raw["twofa"]["pending_secret"]
    assert u.post("/api/auth/2fa/totp/enable", json={"code": totp.current(setup["secret"])}).status_code == 200
    raw = users_collection.find_one({"email": u.user["email"]})
    assert raw["twofa"]["secret"].startswith("enc:v1:") and setup["secret"] not in raw["twofa"]["secret"]
    ch = client.post("/api/auth/login", json={"email": u.user["email"], "password": "secret123"}).json()
    assert ch["requires_2fa"]
    ok = client.post("/api/auth/2fa/verify", json={"challenge_token": ch["challenge_token"], "code": totp.current(setup["secret"])})
    assert ok.status_code == 200 and ok.json()["access_token"]


def test_a_plain_text_seed_saved_before_this_update_still_logs_in(make_actor, client):
    u = make_actor("user")
    seed = totp.new_secret()
    users_collection.update_one({"email": u.user["email"]}, {"$set": {"twofa": {"enabled": True, "method": "totp", "secret": seed}}})
    ch = client.post("/api/auth/login", json={"email": u.user["email"], "password": "secret123"}).json()
    ok = client.post("/api/auth/2fa/verify", json={"challenge_token": ch["challenge_token"], "code": totp.current(seed)})
    assert ok.status_code == 200


# ------------------------------------------------------------------ extra rate limits
@pytest.fixture()
def limited(monkeypatch):
    from app.middleware import limiter

    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    limiter.reset()
    yield
    limiter.reset()


def test_support_tickets_are_rate_limited(limited, make_actor):
    u = make_actor("user")
    codes = [u.post("/api/support/tickets", json={"subject": f"Help {i}", "message": "Something is wrong with my account", "category": "other"}).status_code for i in range(12)]
    assert 429 in codes and codes.index(429) == 10, codes


def test_two_factor_email_codes_are_rate_limited(limited, make_actor):
    u = make_actor("user")
    codes = [u.post("/api/auth/2fa/email/send").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5] == 429, codes


def test_regex_rules_cover_apply_and_video_comments(limited, make_actor):
    u = make_actor("user")
    fake = "0" * 24
    codes = [u.post(f"/api/jobs/{fake}/apply", json={}).status_code for _ in range(42)]
    assert codes[39] != 429 and codes[40] == 429, "40 applications per hour per IP, then 429"
    codes = [u.post(f"/api/videos/{fake}/comments", json={"text": "hi"}).status_code for _ in range(32)]
    assert codes[29] != 429 and codes[30] == 429


# ------------------------------------------------------------------ new-device alert
def _alerts(email):
    return list(email_outbox_collection.find({"to": email, "template": "login_alert"}))


def test_new_device_login_sends_an_alert_but_known_devices_and_first_login_do_not(make_actor, client):
    u = make_actor("user")
    email = u.user["email"]
    win = {"user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36"}
    phone = {"user-agent": "Mozilla/5.0 (iPhone) Safari/604"}
    login = lambda h: client.post("/api/auth/login", json={"email": email, "password": "secret123"}, headers=h)
    assert _alerts(email) == [], "registering is not an alert"
    login(win)
    assert _alerts(email) == [] or len(_alerts(email)) == 1  # first browser differs from the registration client: at most one alert
    base = len(_alerts(email))
    login(win)
    assert len(_alerts(email)) == base, "same device again: no alert"
    login(phone)
    rows = _alerts(email)
    assert len(rows) == base + 1 and "iOS" in rows[-1]["body"] and "change your password" in rows[-1]["body"]
    notes = u.get("/api/notifications").json()
    assert any("New sign-in" in n["title"] for n in notes.get("items", notes if isinstance(notes, list) else []))


# ------------------------------------------------------------------ admin audit trail
def test_admin_job_company_application_and_video_actions_are_audited(admin, recruiter, live_job):
    jid = live_job["id"] if isinstance(live_job, dict) else live_job
    admin.patch(f"/api/admin/jobs/{jid}/feature")
    admin.patch(f"/api/admin/jobs/{jid}/toggle")
    assert admin.patch(f"/api/admin/jobs/{jid}/status", json={"status": "rejected", "reason": "Needs more detail"}).status_code == 200
    admin.patch(f"/api/admin/companies/{recruiter.company['id']}", json={"is_verified": True})
    assert admin.delete(f"/api/admin/jobs/{jid}").status_code == 200
    actions = {a["action"] for a in audit_logs_collection.find({})}
    assert {"job.feature", "job.toggle", "job.status", "company.moderate", "job.delete"} <= actions
    row = audit_logs_collection.find_one({"action": "job.status", "detail": {"$regex": "rejected"}})
    assert "approved -> rejected" in row["detail"] and row["admin_email"] == "admin@carevo.test"


# ------------------------------------------------------------------ robots / sitemap / share page
def test_robots_and_sitemap_list_only_live_jobs(client, admin, live_job):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /api/" in r.text
    jid = live_job["id"] if isinstance(live_job, dict) else live_job
    s = client.get("/api/public/sitemap.xml")
    assert s.status_code == 200 and s.headers["content-type"].startswith("application/xml") and f"{config.PUBLIC_APP_URL}/jobs/{jid}" in s.text
    admin.patch(f"/api/admin/jobs/{jid}/toggle")  # hidden jobs leave the sitemap
    assert f"/jobs/{jid}" not in client.get("/api/public/sitemap.xml").text


def test_share_page_has_open_graph_tags_and_escapes_text(client, admin, recruiter):
    job = recruiter.post("/api/recruiter/jobs", json={**JOB, "title": 'Dev "><script>alert(1)</script>', "description": "Build <b>things</b> & more"}).json()
    jid = job["id"]
    admin.patch(f"/api/admin/jobs/{jid}/status", json={"status": "approved"})
    r = client.get(f"/api/public/share/jobs/{jid}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert 'property="og:title"' in r.text and "Acme Corp" in r.text and f'content="0;url={config.PUBLIC_APP_URL}/jobs/{jid}"' in r.text
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text
    assert "<b>things</b>" not in r.text


def test_share_page_is_404_for_unapproved_or_unknown_jobs(client, recruiter):
    job = recruiter.post("/api/recruiter/jobs", json=JOB).json()  # pending review
    assert client.get(f"/api/public/share/jobs/{job['id']}").status_code == 404
    assert client.get("/api/public/share/jobs/not-an-id").status_code == 404


# ------------------------------------------------------------------ direct upload to Cloudinary
def _ticket_ok(t):
    f = dict(t["fields"])
    sig = f.pop("signature"); f.pop("api_key")
    return sig == hashlib.sha1(("&".join(f"{k}={f[k]}" for k in sorted(f)) + "test-secret").encode()).hexdigest()


def test_direct_upload_ticket_is_signed_and_pinned_to_the_users_folder(recruiter, fake_cloudinary):
    t = recruiter.post("/api/media/direct-upload/ticket").json()
    assert t["direct"] and t["upload_url"].endswith("/test-cloud/video/upload") and _ticket_ok(t)
    assert t["fields"]["allowed_formats"] == "mp4,mov,webm" and f"/video/{recruiter.user['id']}/" in t["fields"]["public_id"]
    assert t["fields"]["api_key"] == "test-key" and "test-secret" not in str(t)
    t2 = recruiter.post("/api/media/direct-upload/ticket").json()
    assert t2["fields"]["public_id"] != t["fields"]["public_id"], "one-off id per ticket"


def test_direct_upload_complete_only_accepts_files_from_that_ticket_folder(recruiter, make_actor, fake_cloudinary):
    uid = recruiter.user["id"]
    good = f"https://res.cloudinary.com/test-cloud/video/upload/v1/carevo/video/{uid}/abc123.mp4"
    assert recruiter.post("/api/media/direct-upload/complete", json={"url": good}).status_code == 201
    assert recruiter.post("/api/media/direct-upload/complete", json={"url": good}).status_code == 409, "no double registration"
    bad = [
        f"https://res.cloudinary.com/other-cloud/video/upload/v1/carevo/video/{uid}/x.mp4",
        "https://res.cloudinary.com/test-cloud/video/upload/v1/carevo/video/someone-else/x.mp4",
        f"https://evil.example/carevo/video/{uid}/x.mp4",
        f"https://res.cloudinary.com/test-cloud/video/upload/v1/carevo/video/{uid}/x.mp4?x=1",
        f"https://res.cloudinary.com/test-cloud/video/upload/../raw/{uid}/x.mp4",
    ]
    for u in bad:
        assert recruiter.post("/api/media/direct-upload/complete", json={"url": u}).status_code == 422, u
    cand = make_actor("user")
    assert cand.post("/api/media/direct-upload/ticket").status_code == 403
    assert cand.post("/api/media/direct-upload/complete", json={"url": good}).status_code == 403


def test_direct_upload_falls_back_when_cloudinary_is_not_configured(recruiter):
    t = recruiter.post("/api/media/direct-upload/ticket").json()
    assert t["direct"] is False and t["max_mb"] > 0
    assert recruiter.post("/api/media/direct-upload/complete", json={"url": "https://res.cloudinary.com/x/video/upload/a.mp4"}).status_code == 403


def test_a_direct_upload_url_can_be_used_to_create_a_video(recruiter, fake_cloudinary):
    uid = recruiter.user["id"]
    url = f"https://res.cloudinary.com/test-cloud/video/upload/v1/carevo/video/{uid}/vid1.mp4"
    assert recruiter.post("/api/media/direct-upload/complete", json={"url": url}).status_code == 201
    r = recruiter.post("/api/videos", json={"title": "Day at Acme", "description": "Office tour video", "video_url": url, "category": "culture"})
    assert r.status_code == 201, r.text
    assert "f_mp4" in r.json()["playback_url"] and r.json()["poster_url"]


# ------------------------------------------------------------------ optional error monitoring
def test_error_monitoring_is_off_without_a_dsn_and_never_crashes_the_app(monkeypatch):
    from app.main import init_error_monitoring

    monkeypatch.setattr(config, "SENTRY_DSN", "")
    assert init_error_monitoring() is False
    monkeypatch.setattr(config, "SENTRY_DSN", "https://abc@o1.ingest.sentry.io/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # package not installed
    assert init_error_monitoring() is False


# ------------------------------------------------------------------ X-Forwarded-For rate-limit bypass (pentest finding)
def test_client_ip_trusts_the_last_xff_hop_not_the_client_supplied_first_one(monkeypatch):
    """A client can put anything it likes at the FRONT of X-Forwarded-For. Only the entry a real reverse proxy
    appends (the one nearest us) can be trusted. Taking the first entry lets an attacker defeat every IP-based
    rate limit by sending a fresh made-up value on each request; this is what this test guards against."""
    from app import config
    from app.middleware import client_ip

    class FakeConn:
        host = "10.0.0.5"  # the proxy's own address, as seen by our server

    class FakeReq:
        def __init__(self, xff):
            self.headers = {"x-forwarded-for": xff} if xff is not None else {}
            self.client = FakeConn()

    monkeypatch.setattr(config, "TRUST_PROXY", False)
    assert client_ip(FakeReq("1.2.3.4")) == "10.0.0.5", "TRUST_PROXY off: header must be ignored entirely"

    monkeypatch.setattr(config, "TRUST_PROXY", True)
    assert client_ip(FakeReq("203.0.113.9, 198.18.0.42")) == "198.18.0.42", "the hop nearest us (last) wins"
    assert client_ip(FakeReq("198.18.0.77")) == "198.18.0.77", "a single legitimate hop still works"
    assert client_ip(FakeReq("  203.0.113.1 ,  198.18.0.42  ")) == "198.18.0.42", "whitespace around entries"
    assert client_ip(FakeReq(None)) == "10.0.0.5", "no header at all falls back to the socket peer"
    assert client_ip(FakeReq("")) == "10.0.0.5", "an empty header is not treated as a real hop"
    # an attacker rotating the first entry on every request must not change the key we rate-limit on
    keys = {client_ip(FakeReq(f"{i}.{i}.{i}.{i}, 198.18.0.42")) for i in range(1, 6)}
    assert keys == {"198.18.0.42"}, f"spoofed first hop changed the rate-limit key: {keys}"


def test_register_rate_limit_is_not_defeated_by_spoofing_the_first_xff_hop(monkeypatch):
    """End-to-end: with TRUST_PROXY on (as needed behind Render/Vercel), an attacker who varies only the
    client-supplied first X-Forwarded-For hop is still limited, because the trusted (last) hop stays constant."""
    from app import config
    from app.middleware import limiter

    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "TRUST_PROXY", True)
    limiter.reset()
    from fastapi.testclient import TestClient
    from app.main import app

    c = TestClient(app)
    codes = []
    for i in range(14):
        r = c.post(
            "/api/auth/register",
            json={"name": "Attacker", "email": f"xffbypass{i}@example.com", "password": "secret123", "role": "user"},
            headers={"X-Forwarded-For": f"{i}.{i}.{i}.{i}, 203.0.113.250"},  # spoofs a new first hop every time
        )
        codes.append(r.status_code)
    assert 429 in codes, f"spoofing the first X-Forwarded-For hop defeated the rate limit: {codes}"
    limiter.reset()
