import os
import sys
import tempfile
from pathlib import Path

# Must be set before the app is imported.
os.environ["MONGO_URI"] = "mongomock://"
os.environ["JWT_SECRET"] = "unit-test-secret-unit-test-secret"
os.environ["ADMIN_EMAIL"] = "admin@carevo.test"
os.environ["ADMIN_PASSWORD"] = "Admin@12345"
os.environ["EXPOSE_RESET_TOKEN"] = "true"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="carevo-uploads-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import db, ensure_indexes  # noqa: E402
from app.main import app  # noqa: E402
from app.services.seed import run_startup_seed  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    for name in db.list_collection_names():
        db.drop_collection(name)
    ensure_indexes()
    run_startup_seed()
    yield


@pytest.fixture()
def client():
    return TestClient(app)


class Actor:
    def __init__(self, client, token, user):
        self.c, self.token, self.user = client, token, user
        self.h = {"Authorization": f"Bearer {token}"}
        self.id = user["id"]

    def get(self, url, **kw):
        return self.c.get(url, headers=self.h, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self.h, **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self.h, **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self.h, **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self.h, **kw)


@pytest.fixture()
def make_actor(client):
    counter = {"n": 0}

    def _make(role="user", name=None):
        counter["n"] += 1
        email = f"{role}{counter['n']}@example.com"
        r = client.post(
            "/api/auth/register",
            json={"name": name or f"{role.title()} {counter['n']}", "email": email, "password": "secret123", "role": role},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        return Actor(client, body["access_token"], body["user"])

    return _make


@pytest.fixture()
def admin(client):
    r = client.post("/api/auth/login", json={"email": "admin@carevo.test", "password": "Admin@12345"})
    assert r.status_code == 200, r.text
    return Actor(client, r.json()["access_token"], r.json()["user"])


@pytest.fixture()
def recruiter(make_actor, admin):
    rec = make_actor("recruiter", "Rita Recruiter")
    r = rec.post(
        "/api/recruiter/company",
        json={"name": "Acme Corp", "description": "We build things", "industry": "Software", "size": "sme", "location": "Pune"},
    )
    assert r.status_code == 201, r.text
    rec.company = r.json()
    admin.patch(f"/api/admin/companies/{rec.company['id']}", json={"status": "approved"})
    return rec


JOB = {
    "title": "Senior React Developer",
    "description": "Build awesome user interfaces for our product.",
    "skills": ["React", "TypeScript"],
    "requirements": ["3+ years React"],
    "responsibilities": ["Ship features"],
    "job_type": "fulltime",
    "salary_min": 800000,
    "salary_max": 1500000,
    "location": "Remote",
}


@pytest.fixture()
def live_job(recruiter, admin):
    """An approved job with an external apply link."""
    r = recruiter.post("/api/recruiter/jobs", json={**JOB, "apply_url": "https://careers.acme.test/jobs/react-dev"})
    assert r.status_code == 201, r.text
    job = r.json()
    r = admin.patch(f"/api/admin/jobs/{job['id']}/status", json={"status": "approved"})
    assert r.status_code == 200, r.text
    return r.json()


def make_docx(text: str) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    paras = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n"))
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>{paras}</w:body></w:document>')
    return buf.getvalue()


# ---------------------------------------------------------------- helpers for newer suites
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
MP3 = b"ID3" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


@pytest.fixture()
def fake_cloudinary(monkeypatch):
    """A tiny local stand-in for api.cloudinary.com that verifies the request signature."""
    import hashlib
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs

    from app import config

    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            ctype = self.headers["Content-Type"]
            fields = {}
            if ctype.startswith("multipart/form-data"):
                boundary = ctype.split("boundary=")[1].encode()
                for part in body.split(b"--" + boundary):
                    if b"Content-Disposition" not in part:
                        continue
                    head, _, data = part.partition(b"\r\n\r\n")
                    name = head.split(b'name="')[1].split(b'"')[0].decode()
                    fields[name] = "<file>" if b"filename=" in head else data.rstrip(b"\r\n").decode()
            else:
                fields = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            signed = {k: v for k, v in fields.items() if k not in ("file", "api_key", "signature")}
            expect = hashlib.sha1(("&".join(f"{k}={signed[k]}" for k in sorted(signed)) + "test-secret").encode()).hexdigest()
            ok = fields.get("signature") == expect and fields.get("api_key") == "test-key"
            calls.append({"path": self.path, "fields": fields, "ok": ok})
            if not ok:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(json.dumps({"error": {"message": "Invalid Signature"}}).encode())
                return
            out = {"secure_url": f"https://res.cloudinary.com/test-cloud/{self.path.split('/')[-2]}/upload/v1/{fields.get('folder', '')}/abc123", "public_id": f"{fields.get('folder', '')}/abc123"} if self.path.endswith("/upload") else {"result": "ok"}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(out).encode())

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "CLOUDINARY_CLOUD_NAME", "test-cloud")
    monkeypatch.setattr(config, "CLOUDINARY_API_KEY", "test-key")
    monkeypatch.setattr(config, "CLOUDINARY_API_SECRET", "test-secret")
    monkeypatch.setattr(config, "CLOUDINARY_API_BASE", f"http://127.0.0.1:{server.server_port}/v1_1")
    yield calls
    server.shutdown()
