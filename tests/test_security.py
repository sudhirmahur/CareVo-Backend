"""Security checks: rate limiting, lockout, sessions, headers, injection, privilege escalation, config guards."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture()
def limited(monkeypatch):
    from app import config
    from app.middleware import limiter

    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    limiter.reset()
    yield
    limiter.reset()


def login(client, email, pw):
    return client.post("/api/auth/login", json={"email": email, "password": pw})


# ------------------------------------------------------------------ rate limiting / lockout
def test_login_endpoint_is_rate_limited_per_ip(client, limited):
    codes = [login(client, "nobody@example.com", "whatever1").status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    r = login(client, "nobody@example.com", "whatever1")
    assert "Retry-After" in r.headers and "Too many requests" in r.json()["detail"]


def test_other_endpoints_not_blocked_by_login_limit(client, limited):
    for _ in range(11):
        login(client, "nobody@example.com", "whatever1")
    assert client.get("/api/health").status_code in (200, 404)  # still reachable
    assert client.get("/api/public/site-content").status_code == 200


def test_forgot_password_and_register_are_limited(client, limited):
    codes = [client.post("/api/auth/forgot-password", json={"email": "a@example.com"}).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    reg = [client.post("/api/auth/register", json={"name": "Test User", "email": f"u{i}@example.com", "password": "secret123"}).status_code for i in range(12)]
    assert reg[:10] == [201] * 10 and reg[10:] == [429, 429]


def test_account_locks_after_repeated_wrong_passwords(client, make_actor):
    u = make_actor("user")
    email = u.user["email"]
    for _ in range(4):
        assert login(client, email, "WrongPass1").status_code == 401
    assert login(client, email, "WrongPass1").status_code == 401  # 5th failure triggers the lock
    r = login(client, email, "secret123")  # even the right password is refused while locked
    assert r.status_code == 429 and "Try again" in r.json()["detail"]


def test_successful_login_resets_failure_counter(client, make_actor):
    u = make_actor("user")
    for _ in range(3):
        login(client, u.user["email"], "WrongPass1")
    assert login(client, u.user["email"], "secret123").status_code == 200
    for _ in range(3):
        login(client, u.user["email"], "WrongPass1")
    assert login(client, u.user["email"], "secret123").status_code == 200  # counter restarted


def test_admin_can_unlock_by_unblocking(client, make_actor, admin):
    u = make_actor("user")
    for _ in range(5):
        login(client, u.user["email"], "WrongPass1")
    assert login(client, u.user["email"], "secret123").status_code == 429
    admin.patch(f"/api/admin/users/{u.id}", json={"is_active": True})
    assert login(client, u.user["email"], "secret123").status_code == 200


def test_unknown_email_and_wrong_password_look_identical(client, make_actor):
    u = make_actor("user")
    a = login(client, u.user["email"], "WrongPass1")
    b = login(client, "ghost@example.com", "WrongPass1")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


# ------------------------------------------------------------------ sessions
def test_password_change_invalidates_older_tokens(client, make_actor):
    u = make_actor("user")
    old = dict(u.h)
    import time

    time.sleep(1.1)  # tokens carry a 1-second timestamp
    r = u.post("/api/auth/change-password", json={"current_password": "secret123", "new_password": "newsecret456"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=old).status_code == 401
    fresh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=fresh).status_code == 200


def test_password_reset_invalidates_sessions(client, make_actor):
    import time

    u = make_actor("user")
    token = client.post("/api/auth/forgot-password", json={"email": u.user["email"]}).json()["reset_token"]
    time.sleep(1.1)
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "resetpass789"}).status_code == 200
    assert client.get("/api/auth/me", headers=u.h).status_code == 401
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "another789x"}).status_code == 400  # single use


def test_role_change_invalidates_old_token(client, make_actor, admin):
    import time

    u = make_actor("user")
    time.sleep(1.1)
    admin.patch(f"/api/admin/users/{u.id}", json={"role": "recruiter"})
    assert client.get("/api/auth/me", headers=u.h).status_code == 401


def test_tampered_and_foreign_tokens_are_rejected(client, make_actor):
    import jwt

    u = make_actor("user")
    forged = jwt.encode({"sub": u.id, "role": "admin"}, "not-the-secret", algorithm="HS256")
    none_alg = jwt.encode({"sub": u.id, "role": "admin"}, "", algorithm="none") if hasattr(jwt, "encode") else ""
    for tok in (forged, none_alg, "abc.def.ghi", "", u.token[:-3] + "xyz"):
        r = client.get("/api/admin/users", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code in (401, 403), tok


def test_blocked_user_token_stops_working(client, make_actor, admin):
    u = make_actor("user")
    admin.patch(f"/api/admin/users/{u.id}", json={"is_active": False})
    assert client.get("/api/auth/me", headers=u.h).status_code == 403


# ------------------------------------------------------------------ passwords
@pytest.mark.parametrize("pw", ["short1", "allletters", "12345678", "password123", "a" * 101 + "1"])
def test_weak_passwords_rejected_everywhere(client, make_actor, admin, pw):
    r = client.post("/api/auth/register", json={"name": "Weak User", "email": "weak@example.com", "password": pw})
    assert r.status_code == 422
    assert admin.post("/api/admin/users", json={"name": "Weak User", "email": "weak2@example.com", "password": pw, "role": "user"}).status_code == 422
    u = make_actor("user")
    assert u.post("/api/auth/change-password", json={"current_password": "secret123", "new_password": pw}).status_code == 422


def test_password_hash_never_leaves_the_server(client, make_actor, admin):
    u = make_actor("user")
    blobs = [
        u.get("/api/auth/me").text, u.get("/api/users/me").text,
        admin.get("/api/admin/users").text, admin.get(f"/api/admin/users/{u.id}").text,
        admin.get("/api/admin/applications").text,
    ]
    assert all("password" not in b.lower() and "argon2" not in b for b in blobs)


# ------------------------------------------------------------------ injection / escalation
def test_nosql_operator_injection_is_refused(client, make_actor):
    make_actor("user")
    for payload in (
        {"email": {"$ne": ""}, "password": {"$ne": ""}},
        {"email": {"$gt": ""}, "password": "x"},
        {"email": "a@example.com", "password": {"$regex": ".*"}},
    ):
        assert client.post("/api/auth/login", json=payload).status_code == 422
    assert client.post("/api/auth/forgot-password", json={"email": {"$ne": None}}).status_code == 422
    assert client.post("/api/auth/reset-password", json={"token": {"$ne": ""}, "password": "secret123"}).status_code == 422


def test_search_params_are_treated_as_text_not_regex(client, make_actor):
    u = make_actor("user")
    for q in (".*", "(", "a{1,", "^(a+)+$", "[", "\\"):
        assert u.get("/api/jobs", params={"search": q}).status_code == 200
        assert u.get("/api/companies", params={"search": q}).status_code == 200
    assert client.get("/api/jobs?search[$ne]=x").status_code in (200, 422)


def test_cannot_self_register_as_admin_or_mass_assign(client, make_actor):
    r = client.post("/api/auth/register", json={"name": "Sneaky", "email": "sneaky@example.com", "password": "secret123", "role": "admin"})
    assert r.status_code == 422
    r = client.post("/api/auth/register", json={"name": "Sneaky", "email": "sneaky2@example.com", "password": "secret123", "is_premium": True, "is_active": True})
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["role"] == "user" and me["is_premium"] is False
    u = make_actor("user")
    u.put("/api/users/me", json={"role": "admin", "is_active": True, "email": "x@y.com", "password_hash": "x"})
    me = u.get("/api/auth/me").json()
    assert me["role"] == "user" and me["email"] == u.user["email"]
    assert login(client, u.user["email"], "secret123").status_code == 200


def test_privilege_boundaries_between_roles(make_actor, recruiter, admin):
    user, other = make_actor("user"), make_actor("recruiter")
    admin_urls = ["/api/admin/users", "/api/admin/stats", "/api/admin/jobs", "/api/admin/site-content", "/api/admin/audit-logs", "/api/admin/prep", "/api/admin/assessments", "/api/admin/premium-requests"]
    for url in admin_urls:
        assert user.get(url).status_code == 403, url
        assert other.get(url).status_code == 403, url
    assert user.get("/api/recruiter/jobs").status_code == 403
    assert other.post("/api/users/me/premium/request").status_code == 400
    assert user.post("/api/recruiter/company", json={"name": "Fake Co"}).status_code == 403


def test_idor_other_users_private_data(make_actor, recruiter, admin):
    a, b = make_actor("user"), make_actor("user")
    a.put("/api/resume-builder", json={"full_name": "Alice Secret", "template": "modern"})
    assert "Alice" not in b.get("/api/resume-builder").text
    resume = a.post("/api/resumes", files={"file": ("cv.pdf", b"%PDF-1.4 test", "application/pdf")}, data={"title": "cv"})
    assert resume.status_code == 201, resume.text
    rid = resume.json()["resume_id"]
    assert b.delete(f"/api/resumes/{rid}").status_code == 404
    assert b.get(f"/api/resumes/{rid}").status_code == 404
    assert b.patch(f"/api/resumes/{rid}/default").status_code == 404
    assert b.post(f"/api/resumes/{rid}/analyze").status_code == 404
    assert a.get(f"/api/resumes/{rid}").status_code == 200
    assert b.get(f"/api/messages/with/{a.id}").json()["items"] == []
    other_rec = make_actor("recruiter")
    v = recruiter.post("/api/videos", json={"title": "My video", "video_url": "https://cdn.test/v.mp4"}).json()
    assert other_rec.put(f"/api/videos/{v['id']}", json={"title": "Hijacked", "video_url": "https://evil.test/x.mp4"}).status_code == 403
    assert other_rec.delete(f"/api/videos/{v['id']}").status_code == 403


def test_xss_and_hostile_urls_cannot_be_stored_in_link_fields(recruiter, make_actor):
    bad = ["javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "vbscript:msgbox", "file:///etc/passwd", "ftp://x.test/a"]
    for u in bad:
        r = recruiter.post("/api/recruiter/jobs", json={"title": "Role X", "description": "A test description here.", "apply_url": u})
        assert r.status_code == 422, u
        assert recruiter.put("/api/recruiter/company", json={"website": u}).status_code == 422, u
        assert recruiter.post("/api/videos", json={"title": "Vid X", "video_url": u}).status_code == 422, u
    user = make_actor("user")
    assert user.put("/api/users/me/profile", json={"github_url": "javascript:alert(1)"}).status_code == 422


# ------------------------------------------------------------------ headers / uploads
def test_security_headers_present(client):
    r = client.get("/api/public/site-content")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert "camera=()" in r.headers["permissions-policy"]


def test_cors_only_allows_configured_origins(client):
    ok = client.options("/api/auth/login", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    bad = client.options("/api/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers


def test_resume_upload_validates_content(make_actor):
    u = make_actor("user")
    r = u.post("/api/resumes", files={"file": ("cv.pdf", b"MZ not a pdf", "application/pdf")}, data={"title": "x"})
    assert r.status_code in (400, 422)


# ------------------------------------------------------------------ google login (Google is mocked)
def test_google_login_flow(client, monkeypatch, make_actor):
    from app.routes import auth

    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    info = {"aud": "client-123", "email": "G.User@Example.com", "email_verified": "true", "sub": "g-1", "name": "Google User"}
    monkeypatch.setattr(auth, "_google_profile", lambda tok: info)
    r = client.post("/api/auth/google", json={"access_token": "ya29.token-token", "role": "user"})
    assert r.status_code == 200 and r.json()["user"]["email"] == "g.user@example.com" and r.json()["user"]["auth_provider"] == "google"
    again = client.post("/api/auth/google", json={"access_token": "ya29.token-token"})
    assert again.json()["user"]["id"] == r.json()["user"]["id"]  # no duplicate account
    # existing password account with the same verified email is signed in, not duplicated
    u = make_actor("user")
    monkeypatch.setattr(auth, "_google_profile", lambda tok: {**info, "email": u.user["email"]})
    assert client.post("/api/auth/google", json={"access_token": "ya29.token-token"}).json()["user"]["id"] == u.id
    # the Google user cannot log in with a guessable password
    assert login(client, "g.user@example.com", "secret123").status_code == 401


def test_google_token_must_belong_to_our_client_and_be_verified(client, monkeypatch):
    from app.routes import auth

    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")

    class Resp:
        def __init__(self, data):
            self.data = data

        def read(self):
            import json

            return json.dumps(self.data).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(info):
        monkeypatch.setattr(auth.urllib.request, "urlopen", lambda url, timeout=0: Resp(info))

    fake({"aud": "someone-elses-app", "email": "a@example.com", "email_verified": "true"})
    assert client.post("/api/auth/google", json={"access_token": "ya29.token-token"}).status_code == 401
    fake({"aud": "client-123", "email": "a@example.com", "email_verified": "false"})
    assert client.post("/api/auth/google", json={"access_token": "ya29.token-token"}).status_code == 401
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda url, timeout=0: (_ for _ in ()).throw(OSError("down")))
    assert client.post("/api/auth/google", json={"access_token": "ya29.token-token"}).status_code == 401


def test_google_login_disabled_when_not_configured(client):
    assert client.post("/api/auth/google", json={"access_token": "ya29.token-token"}).status_code == 501


# ------------------------------------------------------------------ production guards
def _boot(**env):
    e = {k: v for k, v in os.environ.items() if k not in ("JWT_SECRET", "ENV", "EXPOSE_RESET_TOKEN", "CORS_ORIGINS")}
    e.update({"MONGO_URI": "mongomock://", "PYTHONPATH": os.environ.get("PYTHONPATH", "") + os.pathsep + str(BACKEND), **env})
    return subprocess.run([sys.executable, "-c", "import app.config"], cwd=BACKEND, env=e, capture_output=True, text=True)


def test_production_refuses_weak_configuration():
    assert _boot(ENV="production").returncode != 0  # no JWT secret
    assert "JWT_SECRET" in _boot(ENV="production", JWT_SECRET="short").stderr
    assert "EXPOSE_RESET_TOKEN" in _boot(ENV="production", JWT_SECRET="x" * 40, EXPOSE_RESET_TOKEN="true").stderr
    assert "CORS_ORIGINS" in _boot(ENV="production", JWT_SECRET="x" * 40, CORS_ORIGINS="*").stderr
    assert _boot(ENV="production", JWT_SECRET="x" * 40, CORS_ORIGINS="https://carevo.example").returncode == 0


def test_docs_hidden_in_production():
    code = "import app.main as m; print(m.app.docs_url, m.app.openapi_url)"
    e = {**{k: v for k, v in os.environ.items() if k != "EXPOSE_RESET_TOKEN"}, "MONGO_URI": "mongomock://", "ENV": "production", "JWT_SECRET": "x" * 40, "CORS_ORIGINS": "https://carevo.example", "PYTHONPATH": os.environ.get("PYTHONPATH", "") + os.pathsep + str(BACKEND)}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=e, capture_output=True, text=True)
    assert out.stdout.strip().endswith("None None"), out.stderr


def test_resumes_are_private_signed_downloads(client, make_actor, recruiter, admin):
    from tests.conftest import JOB

    owner, stranger = make_actor("user"), make_actor("user")
    rid = owner.post("/api/resumes", files={"file": ("cv.pdf", b"%PDF-1.4 secret resume", "application/pdf")}).json()
    file_url = owner.get("/api/resumes").json()[0]["file_url"]
    assert client.get(file_url).status_code == 404, "raw upload path must not serve resumes"
    link = owner.get(f"/api/resumes/{rid['resume_id']}/link")
    assert link.status_code == 200
    got = client.get(link.json()["url"])
    assert got.status_code == 200 and got.content.startswith(b"%PDF") and "attachment" in got.headers["content-disposition"]
    # strangers, other roles and forged / expired links are refused
    assert stranger.get(f"/api/resumes/{rid['resume_id']}/link").status_code == 404
    assert client.get(link.json()["url"].replace("sig=", "sig=0")).status_code == 403
    assert client.get(f"/api/files/resume/{rid['resume_id']}?exp=1&sig=abc").status_code == 403
    # the recruiter who received an application with that resume may open it; others may not
    job = recruiter.post("/api/recruiter/jobs", json=JOB).json()
    admin.patch(f"/api/admin/jobs/{job['id']}/status", json={"status": "approved"})
    other_rec = make_actor("recruiter")
    assert recruiter.get(f"/api/resumes/{rid['resume_id']}/link").status_code == 404  # no application yet
    assert owner.post(f"/api/jobs/{job['id']}/apply", json={}).status_code == 201
    assert recruiter.get(f"/api/resumes/{rid['resume_id']}/link").status_code == 200
    assert other_rec.get(f"/api/resumes/{rid['resume_id']}/link").status_code == 404
    assert admin.get(f"/api/resumes/{rid['resume_id']}/link").status_code == 200
