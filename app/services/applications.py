"""Shape application documents for the three panels."""
from app.database import (
    interviews_collection,
    jobs_collection,
    resumes_collection,
    user_profiles_collection,
    user_skills_collection,
    skills_collection,
    users_collection,
)
from app.utils import ser


def _job_brief(job: dict | None, app: dict) -> dict:
    if job:
        return {
            "id": str(job["_id"]),
            "title": job.get("title"),
            "location": job.get("location"),
            "job_type": job.get("job_type"),
            "apply_url": job.get("apply_url"),
            "is_active": job.get("is_active", True),
            "status": job.get("status"),
        }
    # job deleted: fall back to the snapshot saved at apply time
    return {
        "id": str(app["job_id"]),
        "title": app.get("job_title"),
        "location": None,
        "job_type": None,
        "apply_url": app.get("apply_url"),
        "is_active": False,
        "status": "removed",
    }


def shape_applications(apps: list, audience: str = "user") -> list:
    jobs = {j["_id"]: j for j in jobs_collection.find({"_id": {"$in": [a["job_id"] for a in apps]}})}
    interviews = {}
    for iv in interviews_collection.find({"application_id": {"$in": [a["_id"] for a in apps]}}):
        interviews.setdefault(iv["application_id"], []).append(iv)

    users, profiles, resumes = {}, {}, {}
    if audience in ("recruiter", "admin"):
        uids = [a["user_id"] for a in apps]
        users = {u["_id"]: u for u in users_collection.find({"_id": {"$in": uids}})}
        profiles = {p["user_id"]: p for p in user_profiles_collection.find({"user_id": {"$in": uids}})}
    rids = [a["resume_id"] for a in apps if a.get("resume_id")]
    if rids:
        resumes = {r["_id"]: r for r in resumes_collection.find({"_id": {"$in": rids}})}

    out = []
    for a in apps:
        item = ser(a)
        item["job"] = _job_brief(jobs.get(a["job_id"]), a)
        item["job_title"] = item["job"]["title"]
        item["company_name"] = a.get("company_name")
        resume = resumes.get(a.get("resume_id"))
        item["resume"] = (
            {"id": str(resume["_id"]), "file_url": resume.get("file_url"), "name": resume.get("original_name"), "ai_score": resume.get("ai_score")}
            if resume
            else None
        )
        item["interviews"] = [ser(iv) for iv in sorted(interviews.get(a["_id"], []), key=lambda x: x["scheduled_at"])]
        if audience in ("recruiter", "admin"):
            u = users.get(a["user_id"])
            p = profiles.get(a["user_id"], {})
            if u:
                names = []
                rows = list(user_skills_collection.find({"user_id": u["_id"]}))
                if rows:
                    names = [s["name"] for s in skills_collection.find({"_id": {"$in": [r["skill_id"] for r in rows]}})]
                item["candidate"] = {
                    "id": str(u["_id"]),
                    "name": u["name"],
                    "email": u["email"],
                    "phone": u.get("phone"),
                    "profile_image": u.get("profile_image"),
                    "current_position": p.get("current_position"),
                    "experience_years": p.get("experience_years", 0),
                    "location": p.get("location"),
                    "bio": p.get("bio"),
                    "skills": sorted(set(p.get("skills", [])) | set(names)),
                    "github_url": p.get("github_url"),
                    "linkedin_url": p.get("linkedin_url"),
                    "portfolio_url": p.get("portfolio_url"),
                }
            else:
                item["candidate"] = {"id": str(a["user_id"]), "name": "Deleted user"}
        out.append(item)
    return out
