from tests.conftest import make_docx

CV = (
    "Jane Doe\njane@doe.com\n+91 98765 43210\nSummary\nFull stack developer\nExperience\n"
    "Built and optimized React and Python services, reduced load time by 40%. Led a team of 5.\n"
    "Launched a FastAPI platform serving 10k users. Developed MongoDB pipelines and implemented Docker CI/CD.\n"
    "Education\nB.Tech Computer Science\nSkills\nReact, Python, FastAPI, MongoDB, Docker, Git\nProjects\nCarevo\n"
    "github.com/janedoe linkedin.com/in/janedoe\n"
) + ("Worked closely with product teams to deliver features. " * 12)


def test_resume_upload_rules(make_actor):
    u = make_actor("user")
    assert u.post("/api/resumes", files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 400
    assert u.post("/api/resumes", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400  # bad magic
    assert u.post("/api/resumes", files={"file": ("x.pdf", b"", "application/pdf")}).status_code == 400
    assert u.post("/api/resumes", files={"file": ("x.pdf", b"%PDF-1.4" + b"0" * (6 * 1024 * 1024), "application/pdf")}).status_code == 400


def test_resume_lifecycle_and_default(make_actor):
    u = make_actor("user")
    r1 = u.post("/api/resumes", files={"file": ("one.docx", make_docx("a"), "application/octet-stream")}).json()
    r2 = u.post("/api/resumes", files={"file": ("two.docx", make_docx("b"), "application/octet-stream")}).json()
    assert r1["is_default"] is True and r2["is_default"] is False  # first upload becomes default
    lst = u.get("/api/resumes").json()
    assert {x["name"] for x in lst} == {"one.docx", "two.docx"}
    assert u.patch(f"/api/resumes/{r2['resume_id']}/default").status_code == 200
    assert [x["id"] for x in u.get("/api/resumes").json() if x["is_default"]] == [r2["resume_id"]]
    assert u.get("/api/users/me/profile").json()["resume_id"] == r2["resume_id"]
    # deleting the default promotes another
    assert u.delete(f"/api/resumes/{r2['resume_id']}").status_code == 200
    assert [x["id"] for x in u.get("/api/resumes").json() if x["is_default"]] == [r1["resume_id"]]
    assert u.get(f"/api/resumes/{r2['resume_id']}").status_code == 404


def test_resumes_are_private(make_actor):
    a, b = make_actor("user"), make_actor("user")
    rid = a.post("/api/resumes", files={"file": ("a.docx", make_docx("a"), "application/octet-stream")}).json()["resume_id"]
    assert b.get(f"/api/resumes/{rid}").status_code == 404
    assert b.delete(f"/api/resumes/{rid}").status_code == 404
    assert b.post(f"/api/resumes/{rid}/analyze").status_code == 404
    assert b.get("/api/resumes").json() == []


def test_resume_analysis_scores_and_suggests(make_actor):
    u = make_actor("user")
    u.put("/api/users/me/profile", json={"skills": ["Kubernetes"]})
    rid = u.post("/api/resumes", files={"file": ("cv.docx", make_docx(CV), "application/octet-stream")}).json()["resume_id"]
    r = u.post(f"/api/resumes/{rid}/analyze")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ai_score"] >= 70
    assert {"experience", "education", "skills"} <= set(d["parsed_data"]["sections"])
    assert "React" in d["parsed_data"]["skills_found"] and d["parsed_data"]["emails"] == ["jane@doe.com"]
    assert any("Kubernetes" in s for s in d["suggestions"])  # profile skill missing from resume
    assert u.get(f"/api/resumes/{rid}").json()["ai_score"] == d["ai_score"]  # persisted


def test_weak_resume_scores_low(make_actor):
    u = make_actor("user")
    rid = u.post("/api/resumes", files={"file": ("cv.docx", make_docx("hello there"), "application/octet-stream")}).json()["resume_id"]
    d = u.post(f"/api/resumes/{rid}/analyze").json()
    assert d["ai_score"] < 30 and len(d["suggestions"]) >= 4


def test_user_dashboard(make_actor, live_job):
    u = make_actor("user")
    u.post(f"/api/jobs/{live_job['id']}/apply", json={})
    u.post(f"/api/jobs/{live_job['id']}/save")
    d = u.get("/api/users/me/dashboard").json()
    assert d["applications_total"] == 1 and d["saved_jobs"] == 1 and d["applications_by_status"] == {"applied": 1}
    assert 0 <= d["profile_completeness"] <= 100 and d["open_jobs"] == 1
