"""Dynamic application questions (8 types), file answers, apply methods, private Cloudinary storage, video URLs."""
import io

import pytest

from tests.conftest import JOB

PDF = ("cv.pdf", b"%PDF-1.4 resume", "application/pdf")
ALL_TYPES = [
    {"id": "s", "type": "text", "question": "Short text question", "required": False},
    {"id": "l", "type": "long_text", "question": "Long text question", "required": False},
    {"id": "c", "type": "choice", "question": "Single choice question", "options": ["A", "B"], "required": False},
    {"id": "m", "type": "multiple", "question": "Multiple choice question", "options": ["X", "Y", "Z"], "required": False},
    {"id": "y", "type": "yesno", "question": "Yes or no question", "required": False},
    {"id": "n", "type": "number", "question": "Number question", "required": False},
    {"id": "e", "type": "email", "question": "Email question", "required": False},
    {"id": "f", "type": "file", "question": "File upload question", "required": False},
]


def _job(recruiter, admin, qs, **extra):
    r = recruiter.post("/api/recruiter/jobs", json={**JOB, "screening_questions": qs, **extra})
    assert r.status_code == 201, r.text
    admin.patch(f"/api/admin/jobs/{r.json()['id']}/status", json={"status": "approved"})
    return r.json()


def _qid(job, key):
    """Question ids are kept when the client sends them; map by the question text for safety."""
    return next(q["id"] for q in job["screening_questions"] if q["question"].lower().startswith(key))


def _ready(user):
    assert user.post("/api/resumes", files={"file": PDF}).status_code == 201


# --------------------------------------------------------------------------- creating / editing / deleting
def test_all_eight_types_are_saved_in_order(recruiter, admin):
    job = _job(recruiter, admin, ALL_TYPES)
    assert [q["type"] for q in job["screening_questions"]] == [q["type"] for q in ALL_TYPES]
    assert job["screening_questions"][2]["options"] == ["A", "B"] and job["screening_questions"][0]["options"] == []


def test_reorder_edit_and_delete_questions(recruiter, admin):
    job = _job(recruiter, admin, ALL_TYPES[:3])
    qs = job["screening_questions"]
    r = recruiter.put(f"/api/recruiter/jobs/{job['id']}", json={"screening_questions": [qs[2], qs[0], {**qs[1], "question": "Renamed long question", "required": True}]})
    assert r.status_code == 200, r.text
    got = recruiter.get(f"/api/recruiter/jobs/{job['id']}").json()["screening_questions"]
    assert [q["id"] for q in got] == [qs[2]["id"], qs[0]["id"], qs[1]["id"]]
    assert got[2]["question"] == "Renamed long question" and got[2]["required"] is True
    r = recruiter.put(f"/api/recruiter/jobs/{job['id']}", json={"screening_questions": []})
    assert r.status_code == 200 and recruiter.get(f"/api/recruiter/jobs/{job['id']}").json()["screening_questions"] == []


@pytest.mark.parametrize("bad", [
    {"type": "choice", "question": "Only one option", "options": ["A"]},
    {"type": "multiple", "question": "Duplicate options", "options": ["A", "a"]},
    {"type": "slider", "question": "Unknown type here"},
    {"type": "text", "question": "ab"},
])
def test_invalid_question_definitions_are_rejected(recruiter, bad):
    assert recruiter.post("/api/recruiter/jobs", json={**JOB, "screening_questions": [bad]}).status_code == 422


def test_question_limit(recruiter):
    many = [{"type": "text", "question": f"Question number {i}"} for i in range(26)]
    assert recruiter.post("/api/recruiter/jobs", json={**JOB, "screening_questions": many}).status_code == 422
    ok = recruiter.post("/api/recruiter/jobs", json={**JOB, "screening_questions": many[:25]})
    assert ok.status_code == 201


def test_questions_are_stored_per_job(recruiter, admin):
    a = _job(recruiter, admin, ALL_TYPES[:1])
    b = _job(recruiter, admin, ALL_TYPES[2:4])
    assert len(recruiter.get(f"/api/recruiter/jobs/{a['id']}").json()["screening_questions"]) == 1
    assert len(recruiter.get(f"/api/recruiter/jobs/{b['id']}").json()["screening_questions"]) == 2


# --------------------------------------------------------------------------- answering
def test_valid_answers_for_every_type_are_stored_and_visible_to_the_recruiter(recruiter, admin, make_actor):
    job = _job(recruiter, admin, ALL_TYPES)
    cand = make_actor("user"); _ready(cand)
    up = cand.post(f"/api/jobs/{job['id']}/answer-file", files={"file": ("proof.pdf", b"%PDF-1.4 proof", "application/pdf")})
    assert up.status_code == 201, up.text
    ids = {k: _qid(job, v) for k, v in {"s": "short", "l": "long", "c": "single", "m": "multiple", "y": "yes", "n": "number", "e": "email", "f": "file"}.items()}
    answers = {ids["s"]: "hello", ids["l"]: "a long\nanswer", ids["c"]: "B", ids["m"]: ["X", "Z"], ids["y"]: "yes", ids["n"]: "12.5", ids["e"]: "Me@Example.com", ids["f"]: up.json()["file_id"]}
    r = cand.post(f"/api/jobs/{job['id']}/apply", json={"answers": answers})
    assert r.status_code == 201, r.text
    apps = recruiter.get("/api/recruiter/applications").json()["items"]
    rows = {x["type"]: x for x in apps[0]["answers"]}
    assert rows["multiple"]["values"] == ["X", "Z"] and rows["multiple"]["answer"] == "X, Z"
    assert rows["yesno"]["answer"] == "Yes" and rows["email"]["answer"] == "me@example.com" and rows["number"]["answer"] == "12.5"
    assert rows["file"]["file_name"] == "proof.pdf" and rows["file"]["file_id"] == up.json()["file_id"]


@pytest.mark.parametrize("qtype,value", [
    ("number", "abc"), ("number", "nan"), ("email", "not-an-email"), ("yesno", "maybe"), ("choice", "Z"),
])
def test_invalid_answers_are_rejected(recruiter, admin, make_actor, qtype, value):
    q = {"id": "q", "type": qtype, "question": "A question here", "required": True, "options": ["A", "B"] if qtype == "choice" else []}
    job = _job(recruiter, admin, [q])
    cand = make_actor("user"); _ready(cand)
    assert cand.post(f"/api/jobs/{job['id']}/apply", json={"answers": {_qid(job, "a question"): value}}).status_code == 422


def test_multiple_choice_rules_and_required(recruiter, admin, make_actor):
    job = _job(recruiter, admin, [{"id": "m", "type": "multiple", "question": "Pick some", "options": ["X", "Y"], "required": True}])
    cand = make_actor("user"); _ready(cand)
    qid = _qid(job, "pick")
    for bad in ({}, {qid: []}, {qid: ["Q"]}, {qid: "X"} if False else {qid: ["X", "Nope"]}):
        assert cand.post(f"/api/jobs/{job['id']}/apply", json={"answers": bad}).status_code == 422
    assert cand.post(f"/api/jobs/{job['id']}/apply", json={"answers": {qid: ["Y"]}}).status_code == 201


def test_required_versus_optional_and_unknown_question_ids(recruiter, admin, make_actor):
    job = _job(recruiter, admin, [{"type": "text", "question": "Required one", "required": True}, {"type": "text", "question": "Optional one", "required": False}])
    cand = make_actor("user"); _ready(cand)
    qs = job["screening_questions"]
    assert cand.post(f"/api/jobs/{job['id']}/apply", json={"answers": {qs[1]["id"]: "only optional"}}).status_code == 422
    assert cand.post(f"/api/jobs/{job['id']}/apply", json={"answers": {qs[0]["id"]: "ok", "nope": "x"}}).status_code == 422
    assert cand.post(f"/api/jobs/{job['id']}/apply", json={"answers": {qs[0]["id"]: "ok"}}).status_code == 201


def test_old_applications_and_jobs_without_questions_still_work(recruiter, admin, make_actor):
    job = _job(recruiter, admin, [])
    cand = make_actor("user"); _ready(cand)
    assert cand.post(f"/api/jobs/{job['id']}/apply", json={}).status_code == 201
    assert recruiter.get("/api/recruiter/applications").json()["items"][0]["answers"] == []


# --------------------------------------------------------------------------- file answers
def _file_job(recruiter, admin):
    return _job(recruiter, admin, [{"id": "f", "type": "file", "question": "Upload your portfolio", "required": True}])


def test_file_answer_validation(recruiter, admin, make_actor):
    job, cand = _file_job(recruiter, admin), make_actor("user")
    url = f"/api/jobs/{job['id']}/answer-file"
    assert cand.post(url, files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert cand.post(url, files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400, "content must match the extension"
    assert cand.post(url, files={"file": ("x.pdf", b"", "application/pdf")}).status_code == 400
    assert cand.post(url, files={"file": ("big.pdf", b"%PDF" + b"0" * (8 * 1024 * 1024 + 10), "application/pdf")}).status_code == 400
    plain = _job(recruiter, admin, [])
    assert cand.post(f"/api/jobs/{plain['id']}/answer-file", files={"file": PDF}).status_code == 400, "job without a file question"
    assert recruiter.post(url, files={"file": PDF}).status_code == 403, "only candidates upload answers"


def test_file_answer_access_control(recruiter, admin, make_actor, client):
    job, cand, other = _file_job(recruiter, admin), make_actor("user"), make_actor("user")
    _ready(cand); _ready(other)
    fid = cand.post(f"/api/jobs/{job['id']}/answer-file", files={"file": ("p.pdf", b"%PDF-1.4 secret portfolio", "application/pdf")}).json()["file_id"]
    qid = job["screening_questions"][0]["id"]
    assert other.post(f"/api/jobs/{job['id']}/apply", json={"answers": {qid: fid}}).status_code == 422, "cannot reuse someone else's file"
    assert cand.post(f"/api/jobs/{job['id']}/apply", json={"answers": {qid: fid}}).status_code == 201
    link = recruiter.get(f"/api/recruiter/answer-files/{fid}/link")
    assert link.status_code == 200
    got = client.get(link.json()["url"])
    assert got.status_code == 200 and got.content.startswith(b"%PDF") and "attachment" in got.headers["content-disposition"]
    assert client.get(link.json()["url"].replace("sig=", "sig=0")).status_code == 403
    assert client.get(f"/api/files/answer/{fid}?exp=1&sig=abc").status_code == 403
    assert make_actor("recruiter").get(f"/api/recruiter/answer-files/{fid}/link").status_code == 404, "another recruiter"
    assert cand.get(f"/api/recruiter/answer-files/{fid}/link").status_code == 403, "candidates cannot ask for recruiter links"


# --------------------------------------------------------------------------- external vs internal applications
def test_external_url_is_validated_and_saved(recruiter):
    for bad in ("javascript:alert(1)", "ftp://x.test/a", "not a url", "http://"):
        assert recruiter.post("/api/recruiter/jobs", json={**JOB, "apply_url": bad}).status_code == 422, bad
    ok = recruiter.post("/api/recruiter/jobs", json={**JOB, "apply_url": "https://company.com/careers/software-developer"})
    assert ok.status_code == 201 and ok.json()["apply_url"].startswith("https://company.com/careers/software-developer")


def test_external_apply_redirects_and_internal_needs_a_resume(recruiter, admin, make_actor):
    ext = _job(recruiter, admin, [], apply_url="https://company.com/careers/dev")
    internal = _job(recruiter, admin, [])
    cand = make_actor("user")
    r = cand.post(f"/api/jobs/{ext['id']}/apply", json={})
    assert r.status_code == 201 and r.json()["redirect_url"].startswith("https://company.com/careers/dev")
    again = cand.post(f"/api/jobs/{ext['id']}/apply", json={})
    assert again.json().get("already_applied") is True
    assert cand.post(f"/api/jobs/{internal['id']}/apply", json={}).status_code == 400, "internal needs a resume"
    _ready(cand)
    ok = cand.post(f"/api/jobs/{internal['id']}/apply", json={})
    assert ok.status_code == 201 and not ok.json().get("redirect_url")
    # the recruiter manages the internal application's status and the candidate sees it
    app_id = ok.json()["application_id"]
    assert recruiter.patch(f"/api/recruiter/applications/{app_id}/status", json={"status": "shortlisted"}).status_code == 200
    mine = cand.get("/api/applications").json()["items"]
    assert any(a["status"] == "shortlisted" for a in mine)


# --------------------------------------------------------------------------- Cloudinary: private files + video URLs
class FakeCloudinary:
    """Stands in for api.cloudinary.com so the signed-request code paths run end to end."""

    def __init__(self, monkeypatch):
        from app import config
        from app.services import storage
        self.files, self.calls = {}, []
        monkeypatch.setattr(config, "CLOUDINARY_CLOUD_NAME", "democloud")
        monkeypatch.setattr(config, "CLOUDINARY_API_KEY", "key123")
        monkeypatch.setattr(config, "CLOUDINARY_API_SECRET", "secret456")
        monkeypatch.setattr(storage, "_post", self.post)
        monkeypatch.setattr(storage, "_get", self.get)
        self.storage = storage

    def post(self, url, body, ctype, timeout=120):
        self.calls.append(url)
        if url.endswith("/destroy"):
            pid = dict(p.split("=") for p in body.decode().split("&"))["public_id"]
            from urllib.parse import unquote
            self.files.pop(unquote(pid), None)
            return {"result": "ok"}
        text = body.decode("latin-1")
        assert 'name="signature"' in text and 'name="type"' in text and "authenticated" in text, "private upload must be signed + authenticated"
        pid = text.split('name="public_id"\r\n\r\n')[1].split("\r\n")[0]
        payload = body.split(b"\r\n\r\n", 1)  # crude: keep the whole body, we only need to prove round trip
        self.files[pid] = body
        return {"public_id": pid, "secure_url": f"https://res.cloudinary.com/democloud/raw/authenticated/{pid}"}

    def get(self, url, timeout=60):
        from urllib.parse import parse_qs, unquote, urlparse
        q = parse_qs(urlparse(url).query)
        assert "signature" in q and q["type"] == ["authenticated"]
        pid = unquote(q["public_id"][0])
        if pid not in self.files:
            from fastapi import HTTPException
            raise HTTPException(404, "File not found")
        body = self.files[pid]
        start = body.index(b"\r\n\r\n", body.index(b'name="file"')) + 4
        return body[start:body.rindex(b"\r\n--")]


def test_resumes_and_documents_go_to_cloudinary_and_are_still_private(client, monkeypatch, make_actor, recruiter, admin):
    fake = FakeCloudinary(monkeypatch)
    owner = make_actor("user")
    rid = owner.post("/api/resumes", files={"file": ("cv.pdf", b"%PDF-1.4 cloud resume", "application/pdf")}).json()["resume_id"]
    assert any(c.endswith("/raw/upload") for c in fake.calls) and len(fake.files) == 1
    assert next(iter(fake.files)).startswith("carevo/resumes/")
    link = owner.get(f"/api/resumes/{rid}/link").json()["url"]
    got = client.get(link)
    assert got.status_code == 200 and got.content == b"%PDF-1.4 cloud resume"
    assert client.get("/uploads/resumes/anything.pdf").status_code == 404
    # vault document
    d = owner.post("/api/me/documents", files={"file": ("aws.pdf", b"%PDF-1.4 cloud doc", "application/pdf")}, data={"category": "certificate"})
    assert d.status_code == 201, d.text
    dl = owner.get(f"/api/me/documents/{d.json()['id']}/link").json()["url"]
    assert client.get(dl).content == b"%PDF-1.4 cloud doc"
    # screening-answer file
    job = _file_job(recruiter, admin)
    fid = owner.post(f"/api/jobs/{job['id']}/answer-file", files={"file": ("p.pdf", b"%PDF-1.4 cloud answer", "application/pdf")}).json()["file_id"]
    assert client.get(recruiter.get(f"/api/recruiter/answer-files/{fid}/link").json()["url"]).content == b"%PDF-1.4 cloud answer"
    # deleting removes the stored copies
    before = len(fake.files)
    assert owner.delete(f"/api/resumes/{rid}").status_code == 200
    assert len(fake.files) == before - 1
    assert owner.delete(f"/api/me/documents/{d.json()['id']}").status_code == 200
    assert len(fake.files) == before - 2


MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64


def test_public_media_uses_cloudinary_and_returns_playable_urls(monkeypatch, recruiter, make_actor):
    fake = FakeCloudinary(monkeypatch)
    sent = {}

    def post(url, body, ctype, timeout=120):
        sent["url"] = url
        return {"secure_url": "https://res.cloudinary.com/democloud/video/upload/v1700000000/carevo/video/abc.mov", "public_id": "carevo/video/abc"}

    monkeypatch.setattr(fake.storage, "_post", post)
    up = recruiter.post("/api/media/upload", files={"file": ("clip.mp4", MP4, "video/mp4")}, data={"purpose": "video"})
    assert up.status_code == 201, up.text
    assert sent["url"].endswith("/democloud/video/upload") and up.json()["provider"] == "cloudinary" and up.json()["url"].startswith("https://res.cloudinary.com/")
    v = recruiter.post("/api/videos", json={"title": "Office tour", "video_url": up.json()["url"], "type": "company"})
    assert v.status_code == 201, v.text
    feed = make_actor("user").get("/api/videos").json()["items"][0]
    # an iPhone .mov is delivered as H.264/AAC mp4 so every browser can play it, plus a first-frame poster
    assert feed["video_url"].endswith("abc.mov")
    assert feed["playback_url"] == "https://res.cloudinary.com/democloud/video/upload/f_mp4,vc_h264,ac_aac,q_auto/v1700000000/carevo/video/abc.mp4"
    assert feed["poster_url"] == "https://res.cloudinary.com/democloud/video/upload/so_0,f_jpg,w_720,q_auto/v1700000000/carevo/video/abc.jpg"


def test_playback_url_helpers():
    from app.services.storage import playback_url, poster_url

    assert playback_url("/uploads/media/video/x.mp4") == "/uploads/media/video/x.mp4"
    assert playback_url("https://cdn.example.com/a.mp4") == "https://cdn.example.com/a.mp4"
    assert playback_url(None) is None and poster_url("https://cdn.example.com/a.mp4") is None
    assert poster_url("/uploads/media/video/x.mp4") is None
