"""Fill an empty database with demo data:  python -m app.demo_seed

Creates (idempotently):  admin / recruiter / candidate accounts, one approved company,
several live jobs (some with an external apply link), videos and a banner.
"""
from app.database import (
    banners_collection,
    companies_collection,
    jobs_collection,
    user_profiles_collection,
    users_collection,
    videos_collection,
)
from app.models.users import create_profile_document, create_user_document
from app.services.auth import hash_password
from app.services.jobs import sync_job_skills
from app.services.seed import run_startup_seed
from app.utils import now

DEMO_PASSWORDS = {
    "admin@carevo.demo": "Admin@12345",
    "recruiter@carevo.demo": "Recruiter@123",
    "user@carevo.demo": "User@12345",
}


def _user(name, email, role):
    existing = users_collection.find_one({"email": email})
    if existing:
        return existing
    doc = create_user_document(name, email, hash_password(DEMO_PASSWORDS[email]), role=role)
    doc["_id"] = users_collection.insert_one(dict(doc)).inserted_id
    user_profiles_collection.insert_one(create_profile_document(doc["_id"]))
    return doc


JOBS = [
    ("Senior React Developer", ["React", "TypeScript", "Tailwind CSS"], "Bengaluru", "fulltime", 1800000, 2800000, "https://example.com/careers/senior-react-developer"),
    ("Python Backend Engineer", ["Python", "FastAPI", "MongoDB"], "Remote", "fulltime", 1500000, 2400000, "https://example.com/careers/python-backend-engineer"),
    ("UI/UX Design Intern", ["Figma", "UI/UX"], "Pune", "internship", 15000, 25000, None),
    ("DevOps Engineer", ["Docker", "Kubernetes", "AWS", "CI/CD"], "Hyderabad", "fulltime", 2000000, 3200000, "https://example.com/careers/devops-engineer"),
    ("Flutter Mobile Developer", ["Flutter", "Android"], "Remote", "contract", 90000, 140000, None),
    ("Data Analyst", ["SQL", "Data Analysis", "Pandas"], "Mumbai", "parttime", 40000, 70000, "https://example.com/careers/data-analyst"),
]


def run() -> None:
    run_startup_seed()
    _user("Demo Admin", "admin@carevo.demo", "admin")
    rec = _user("Rita Recruiter", "recruiter@carevo.demo", "recruiter")
    _user("Asha Candidate", "user@carevo.demo", "user")

    company = companies_collection.find_one({"recruiter_id": rec["_id"]})
    if not company:
        ts = now()
        company = {
            "name": "Demo Tech Pvt Ltd",
            "description": "A demo product company building developer tools.",
            "logo_url": None,
            "website": "https://example.com",
            "industry": "Software",
            "size": "sme",
            "location": "Bengaluru",
            "recruiter_id": rec["_id"],
            "is_verified": True,
            "status": "approved",
            "created_at": ts,
            "updated_at": ts,
        }
        company["_id"] = companies_collection.insert_one(dict(company)).inserted_id

    for title, skills, loc, jtype, lo, hi, url in JOBS:
        if jobs_collection.find_one({"title": title, "company_id": company["_id"]}):
            continue
        ts = now()
        job = {
            "company_id": company["_id"],
            "recruiter_id": rec["_id"],
            "title": title,
            "description": f"We are hiring a {title}. You will work with a friendly team on real products.",
            "requirements": ["Strong fundamentals", "Good communication"],
            "responsibilities": ["Build and ship features", "Collaborate with the team"],
            "skills": skills,
            "job_type": jtype,
            "salary_min": lo,
            "salary_max": hi,
            "location": loc,
            "apply_url": url,
            "application_deadline": None,
            "is_active": True,
            "is_featured": title.startswith("Senior"),
            "status": "approved",
            "rejection_reason": None,
            "views_count": 0,
            "applications_count": 0,
            "created_at": ts,
            "updated_at": ts,
        }
        job["_id"] = jobs_collection.insert_one(dict(job)).inserted_id
        sync_job_skills(job["_id"], skills)

    if videos_collection.count_documents({}) == 0:
        ts = now()
        ext = jobs_collection.find_one({"company_id": company["_id"], "apply_url": {"$ne": None}})
        any_job = ext or jobs_collection.find_one({"company_id": company["_id"]})
        for title, vtype in [("We are hiring - join Demo Tech!", "job"), ("A day at Demo Tech", "company"), ("How to ace your interview", "tip")]:
            videos_collection.insert_one(
                {
                    "title": title,
                    "description": "Demo video",
                    "video_url": "https://www.w3schools.com/html/mov_bbb.mp4",
                    "thumbnail_url": None,
                    "type": vtype,
                    "tags": ["demo"],
                    "is_public": True,
                    "user_id": rec["_id"],
                    "company_id": company["_id"],
                    "job_id": any_job["_id"] if (vtype == "job" and any_job) else None,
                    "views_count": 0,
                    "likes_count": 0,
                    "comments_count": 0,
                    "is_removed": False,
                    "created_at": ts,
                    "updated_at": ts,
                }
            )
    if banners_collection.count_documents({}) == 0:
        banners_collection.insert_one({"title": "Hiring season is on!", "image_url": None, "link_url": None, "is_active": True, "created_at": now()})
    print("Demo data ready.")
    for email, pw in DEMO_PASSWORDS.items():
        print(f"  {email}  /  {pw}")


if __name__ == "__main__":
    run()
