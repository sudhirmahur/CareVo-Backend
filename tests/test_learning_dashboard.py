"""Interview prep, skill assessments, resume builder and the new candidate dashboard."""
from tests.conftest import JOB


def test_prep_list_filters_progress_and_categories(make_actor):
    u = make_actor("user")
    cats = u.get("/api/prep/categories").json()["items"]
    assert "Python" in cats and "HR & Behavioral" in cats
    page = u.get("/api/prep?limit=50").json()
    assert page["total"] >= 25 and page["progress"]["known"] == 0
    py = u.get("/api/prep?category=Python").json()["items"]
    assert py and all(i["category"] == "Python" for i in py)
    assert all(i["difficulty"] == "hard" for i in u.get("/api/prep?difficulty=hard").json()["items"])
    assert u.get("/api/prep?q=closure").json()["total"] == 1
    first = py[0]["id"]
    assert u.put(f"/api/prep/{first}/progress", json={"status": "known"}).status_code == 200
    assert u.get("/api/prep?status=known").json()["total"] == 1
    assert u.get("/api/prep?status=none&limit=50").json()["total"] == page["total"] - 1
    assert u.get("/api/prep?limit=50").json()["progress"]["known"] == 1
    assert u.put(f"/api/prep/{first}/progress", json={"status": "bogus"}).status_code == 422
    assert u.put(f"/api/prep/{first}/progress", json={"status": "none"}).status_code == 200
    assert u.get("/api/prep?status=known").json()["total"] == 0


def test_prep_needs_login(client):
    assert client.get("/api/prep").status_code == 401


def test_assessment_never_leaks_answers_and_scores_correctly(make_actor):
    u = make_actor("user")
    items = u.get("/api/assessments").json()["items"]
    assert len(items) == 4 and all(i["best"] is None for i in items)
    a = next(i for i in items if i["skill"] == "Python")
    detail = u.get("/api/assessments/" + a["id"]).json()
    assert len(detail["questions"]) == 8
    assert all(set(q) == {"q", "options"} for q in detail["questions"]), "answers must not be sent to the browser"
    from app.database import assessments_collection
    from bson import ObjectId

    doc = assessments_collection.find_one({"_id": ObjectId(a["id"])})
    perfect = [q["answer"] for q in doc["questions"]]
    res = u.post(f"/api/assessments/{a['id']}/submit", json={"answers": perfect}).json()
    assert res["percent"] == 100 and res["passed"] and all(r["correct"] for r in res["review"])
    wrong = [(q["answer"] + 1) % len(q["options"]) for q in doc["questions"]]
    low = u.post(f"/api/assessments/{a['id']}/submit", json={"answers": wrong}).json()
    assert low["percent"] == 0 and not low["passed"]
    best = next(i for i in u.get("/api/assessments").json()["items"] if i["id"] == a["id"])["best"]
    assert best["percent"] == 100  # best attempt is kept
    assert len(u.get("/api/assessments/results/mine").json()["items"]) == 2
    assert u.get("/api/users/me/profile").json()["verified_skills"] == ["Python"]


def test_assessment_submit_validation(make_actor):
    u = make_actor("user")
    a = u.get("/api/assessments").json()["items"][0]
    assert u.post(f"/api/assessments/{a['id']}/submit", json={"answers": [0]}).status_code == 422
    out = u.post(f"/api/assessments/{a['id']}/submit", json={"answers": [None, 99, -1, 0, 0, 0, 0, 0]}).json()
    assert out["score"] <= 5 and out["review"][1]["correct"] is False
    assert u.get("/api/assessments/not-an-id").status_code == 400


def test_admin_crud_for_prep_and_assessments(admin, make_actor):
    u = make_actor("user")
    q = admin.post("/api/admin/prep", json={"category": "Go", "difficulty": "easy", "question": "What is a goroutine?", "answer": "A lightweight thread managed by the Go runtime."})
    assert q.status_code == 201
    assert u.get("/api/prep?category=Go").json()["total"] == 1
    admin.put(f"/api/admin/prep/{q.json()['id']}", json={"is_active": False})
    assert u.get("/api/prep?category=Go").json()["total"] == 0
    assert admin.delete(f"/api/admin/prep/{q.json()['id']}").status_code == 200

    good = {"title": "Go Basics", "skill": "Go", "questions": [{"q": "Which keyword starts a goroutine?", "options": ["go", "run", "async"], "answer": 0}]}
    r = admin.post("/api/admin/assessments", json=good)
    assert r.status_code == 201
    assert any(i["title"] == "Go Basics" for i in u.get("/api/assessments").json()["items"])
    for bad in (
        {**good, "questions": []},
        {**good, "questions": [{"q": "Pick one", "options": ["a"], "answer": 0}]},
        {**good, "questions": [{"q": "Pick one", "options": ["a", "b"], "answer": 5}]},
        {**good, "pass_percent": 0},
    ):
        assert admin.post("/api/admin/assessments", json=bad).status_code == 422
    assert admin.delete(f"/api/admin/assessments/{r.json()['id']}").status_code == 200
    for actor in (u,):
        assert actor.post("/api/admin/prep", json={"category": "X", "question": "Hello there?", "answer": "General Kenobi"}).status_code == 403
        assert actor.post("/api/admin/assessments", json=good).status_code == 403


def test_resume_builder_prefills_then_saves(make_actor):
    u = make_actor("user", "Priya Sharma")
    u.put("/api/users/me/profile", json={"current_position": "Frontend Dev", "location": "Pune", "skills": ["React", "CSS"], "linkedin_url": "https://linkedin.com/in/priya"})
    first = u.get("/api/resume-builder").json()
    assert first["saved"] is False and first["data"]["full_name"] == "Priya Sharma" and first["data"]["skills"] == ["React", "CSS"]
    data = {**first["data"], "summary": "Builder of UIs", "experience": [{"role": "Dev", "company": "Acme", "start": "2022", "end": "Now", "details": "Did things"}], "template": "classic"}
    assert u.put("/api/resume-builder", json=data).status_code == 200
    again = u.get("/api/resume-builder").json()
    assert again["saved"] is True and again["data"]["summary"] == "Builder of UIs" and again["data"]["template"] == "classic"
    assert u.put("/api/resume-builder", json={**data, "template": "evil"}).status_code == 422
    other = make_actor("user")
    assert other.get("/api/resume-builder").json()["saved"] is False  # private per user


def test_dashboard_has_everything_the_new_ui_needs(recruiter, admin, make_actor):
    j1 = recruiter.post("/api/recruiter/jobs", json={**JOB, "skills": ["React", "Python"]}).json()
    j2 = recruiter.post("/api/recruiter/jobs", json={**JOB, "title": "Data Analyst", "skills": ["SQL"]}).json()
    for j in (j1, j2):
        admin.patch(f"/api/admin/jobs/{j['id']}/status", json={"status": "approved"})
    u = make_actor("user")
    d = u.get("/api/users/me/dashboard").json()
    assert d["stats"]["new_jobs"]["value"] == 2 and d["stats"]["top_companies"]["value"] == 1 and d["stats"]["active_recruiters"]["value"] == 1
    assert d["stats"]["applications"]["value"] == 0
    assert d["completion"]["percent"] == 0 and d["completion"]["next"]["key"] == "photo"
    assert [j["title"] for j in d["recommended_jobs"]][0] in ("Senior React Developer", "Data Analyst")
    assert d["top_companies"][0]["name"] == "Acme Corp" and d["top_companies"][0]["jobs_count"] == 2
    # skills on the profile steer recommendations; applied jobs drop out; activity appears
    u.put("/api/users/me/profile", json={"skills": ["SQL"], "bio": "Analyst", "current_position": "Analyst", "location": "Delhi"})
    d = u.get("/api/users/me/dashboard").json()
    assert d["recommended_jobs"][0]["title"] == "Data Analyst" and d["recommended_jobs"][0]["match_score"] >= 3
    assert d["completion"]["percent"] == 50 and d["profile_completeness"] == 50
    u.post(f"/api/jobs/{j2['id']}/save")
    u.post("/api/jobs/" + j1["id"] + "/apply", json={})  # needs a resume -> 400, nothing recorded
    ext = recruiter.post("/api/recruiter/jobs", json={**JOB, "title": "Ext Role", "apply_url": "https://careers.test/x"}).json()
    admin.patch(f"/api/admin/jobs/{ext['id']}/status", json={"status": "approved"})
    u.post(f"/api/jobs/{ext['id']}/apply", json={})
    recruiter.patch(f"/api/recruiter/applications/{u.get('/api/applications').json()['items'][0]['id']}/status", json={"status": "shortlisted"})
    d = u.get("/api/users/me/dashboard").json()
    kinds = [a["type"] for a in d["recent_activity"]]
    assert "applied" in kinds and "shortlisted" in kinds and "saved" in kinds
    assert d["stats"]["applications"]["value"] == 1
    assert all(j["id"] != ext["id"] for j in d["recommended_jobs"])
