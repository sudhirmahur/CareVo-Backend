"""Startup seeding: default skill taxonomy + first admin account."""
from app.config import ADMIN_EMAIL, ADMIN_NAME, ADMIN_PASSWORD
from app.database import (
    skill_categories_collection,
    skills_collection,
    user_profiles_collection,
    users_collection,
)
from app.models.users import create_profile_document, create_user_document
from app.services.auth import hash_password
from app.utils import now

DEFAULT_SKILLS = {
    "Frontend": ["React", "JavaScript", "TypeScript", "HTML", "CSS", "Vue", "Angular", "Tailwind CSS"],
    "Backend": ["Python", "FastAPI", "Node.js", "Django", "Java", "Spring Boot", "Go", "REST APIs"],
    "Database": ["MongoDB", "PostgreSQL", "MySQL", "Redis", "SQL"],
    "DevOps": ["Docker", "Kubernetes", "AWS", "CI/CD", "Linux", "Git"],
    "Mobile": ["Flutter", "React Native", "Kotlin", "Swift", "Android"],
    "Data & AI": ["Machine Learning", "Data Analysis", "Pandas", "TensorFlow", "NLP"],
    "Design": ["Figma", "UI/UX", "Photoshop"],
    "Soft Skills": ["Communication", "Leadership", "Problem Solving", "Teamwork"],
}


def seed_taxonomy() -> None:
    if skills_collection.count_documents({}) > 0:
        return
    for category, names in DEFAULT_SKILLS.items():
        cat = skill_categories_collection.find_one({"name": category})
        if not cat:
            ts = now()
            cat_id = skill_categories_collection.insert_one(
                {"name": category, "created_at": ts, "updated_at": ts}
            ).inserted_id
        else:
            cat_id = cat["_id"]
        for name in names:
            ts = now()
            skills_collection.update_one(
                {"name": name},
                {"$setOnInsert": {"name": name, "category_id": cat_id, "created_at": ts, "updated_at": ts}},
                upsert=True,
            )


def seed_admin() -> None:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        return
    existing = users_collection.find_one({"email": ADMIN_EMAIL})
    if existing:
        if existing.get("role") != "admin":
            users_collection.update_one({"_id": existing["_id"]}, {"$set": {"role": "admin"}})
        return
    doc = create_user_document(ADMIN_NAME, ADMIN_EMAIL, hash_password(ADMIN_PASSWORD), role="admin")
    user_id = users_collection.insert_one(doc).inserted_id
    user_profiles_collection.insert_one(create_profile_document(user_id))


def run_startup_seed() -> None:
    from app.services.learning_seed import seed_learning

    seed_taxonomy()
    seed_admin()
    seed_learning()
