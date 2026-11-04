"""Data for the candidate dashboard (stats, completion, activity, recommendations)."""
from datetime import timedelta

from app.database import (
    applications_collection,
    companies_collection,
    followers_collection,
    jobs_collection,
    resumes_collection,
    saved_jobs_collection,
    skills_collection,
    user_profiles_collection,
    user_skills_collection,
    users_collection,
)
from app.services.jobs import public_visibility, shape_jobs
from app.utils import now, ser


def _pct(cur: int, prev: int):
    if not prev:
        return None
    return round((cur - prev) / prev * 100)


def _window(collection, query: dict, field: str = "created_at") -> tuple[int, int]:
    t = now()
    cur = collection.count_documents({**query, field: {"$gte": t - timedelta(days=7)}})
    prev = collection.count_documents({**query, field: {"$gte": t - timedelta(days=14), "$lt": t - timedelta(days=7)}})
    return cur, prev


def stats(user: dict) -> dict:
    jobs_cur, jobs_prev = _window(jobs_collection, public_visibility())
    comp_cur, comp_prev = _window(companies_collection, {"status": "approved"})
    rec_cur, rec_prev = _window(users_collection, {"role": "recruiter", "is_active": True})
    app_cur, app_prev = _window(applications_collection, {"user_id": user["_id"]}, "applied_at")
    live_recruiters = len(jobs_collection.distinct("recruiter_id", public_visibility()))
    return {
        "new_jobs": {"value": jobs_cur if jobs_cur else jobs_collection.count_documents(public_visibility()), "change_pct": _pct(jobs_cur, jobs_prev)},
        "top_companies": {"value": companies_collection.count_documents({"status": "approved"}), "change_pct": _pct(comp_cur, comp_prev)},
        "active_recruiters": {"value": live_recruiters, "change_pct": _pct(rec_cur, rec_prev)},
        "applications": {"value": applications_collection.count_documents({"user_id": user["_id"]}), "change_pct": _pct(app_cur, app_prev)},
    }


def my_skill_names(user: dict, profile: dict) -> list[str]:
    names = list(profile.get("skills", []))
    rows = list(user_skills_collection.find({"user_id": user["_id"]}))
    if rows:
        names += [s["name"] for s in skills_collection.find({"_id": {"$in": [r["skill_id"] for r in rows]}})]
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def completion(user: dict, profile: dict) -> dict:
    skills = my_skill_names(user, profile)
    has_resume = resumes_collection.count_documents({"user_id": user["_id"]}) > 0
    items = [
        {"key": "photo", "label": "Add a profile photo", "done": bool(user.get("profile_image")), "link": "/settings"},
        {"key": "position", "label": "Add your current position", "done": bool(profile.get("current_position")), "link": "/profile"},
        {"key": "location", "label": "Add your location", "done": bool(profile.get("location")), "link": "/profile"},
        {"key": "bio", "label": "Write a short bio", "done": bool(profile.get("bio")), "link": "/profile"},
        {"key": "skills", "label": "Add your skills", "done": bool(skills), "link": "/skills"},
        {"key": "resume", "label": "Upload your resume", "done": has_resume, "link": "/resume"},
        {"key": "links", "label": "Add GitHub / LinkedIn / portfolio", "done": any(profile.get(k) for k in ("github_url", "linkedin_url", "portfolio_url")), "link": "/profile"},
        {"key": "experience", "label": "Add your experience", "done": bool(profile.get("experience_years")), "link": "/profile"},
    ]
    done = sum(1 for i in items if i["done"])
    nxt = next((i for i in items if not i["done"]), None)
    return {"percent": round(done / len(items) * 100), "items": items, "next": nxt}


def recent_activity(user: dict, limit: int = 6) -> list:
    uid = user["_id"]
    events = []
    for a in applications_collection.find({"user_id": uid}).sort("applied_at", -1).limit(30):
        title, company = a.get("job_title") or "a job", a.get("company_name") or ""
        for h in a.get("status_history", []):
            st = h.get("status")
            if st == "applied":
                events.append({"type": "applied", "title": f"You applied for {title}", "subtitle": f"at {company}" if company else "", "at": h["at"], "link": "/applications"})
            else:
                events.append({"type": st, "title": f"Your application was {st}", "subtitle": f"{title}" + (f" at {company}" if company else ""), "at": h["at"], "link": "/applications"})
    jobs = {j["_id"]: j for j in jobs_collection.find({"_id": {"$in": [s["job_id"] for s in saved_jobs_collection.find({"user_id": uid})]}})}
    for s in saved_jobs_collection.find({"user_id": uid}).sort("created_at", -1).limit(10):
        j = jobs.get(s["job_id"])
        if j:
            events.append({"type": "saved", "title": f"You saved a job", "subtitle": j.get("title", ""), "at": s["created_at"], "link": f"/jobs/{j['_id']}"})
    for f in followers_collection.find({"follower_id": uid}).sort("created_at", -1).limit(5):
        c = companies_collection.find_one({"_id": f["following_id"]})
        if c:
            events.append({"type": "followed", "title": f"You followed {c['name']}", "subtitle": "", "at": f.get("created_at", now()), "link": f"/companies/{c['_id']}"})
    events.sort(key=lambda e: e["at"], reverse=True)
    return [{**e, "at": ser(e["at"])} for e in events[:limit]]


def top_companies(limit: int = 8) -> list:
    counts: dict = {}
    for j in jobs_collection.find(public_visibility(), {"company_id": 1}):
        if j.get("company_id"):
            counts[j["company_id"]] = counts.get(j["company_id"], 0) + 1
    ids = sorted(counts, key=lambda k: -counts[k])[:limit]
    comps = {c["_id"]: c for c in companies_collection.find({"_id": {"$in": ids}, "status": "approved"})}
    return [{"id": str(i), "name": comps[i]["name"], "logo_url": comps[i].get("logo_url"), "jobs_count": counts[i]} for i in ids if i in comps]


def recommended_jobs(user: dict, profile: dict, limit: int = 5) -> list:
    applied = {a["job_id"] for a in applications_collection.find({"user_id": user["_id"]}, {"job_id": 1})}
    wanted = {s.lower() for s in my_skill_names(user, profile)}
    position = (profile.get("current_position") or "").lower()
    pool = [j for j in jobs_collection.find(public_visibility()).sort("created_at", -1).limit(100) if j["_id"] not in applied]

    def score(j):
        s = len(wanted & {x.lower() for x in j.get("skills", [])}) * 3
        if position and any(w in j.get("title", "").lower() for w in position.split() if len(w) > 3):
            s += 2
        return s

    ranked = sorted(pool, key=lambda j: (-score(j), -j["created_at"].timestamp()))[:limit]
    items = shape_jobs(ranked, user)
    for it, j in zip(items, ranked):
        it["match_score"] = score(j)
    return items
