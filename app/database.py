"""MongoDB connection + collection handles.

Set MONGO_URI=mongomock:// to use an in-memory database (tests/demo only).
"""
from pymongo import ASCENDING, DESCENDING, MongoClient

from app.config import DATABASE_NAME, MONGO_URI

if MONGO_URI.startswith("mongomock://"):
    import mongomock

    client = mongomock.MongoClient(tz_aware=True)
else:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, tz_aware=True)

db = client[DATABASE_NAME]

users_collection = db["users"]
user_profiles_collection = db["user_profiles"]
resumes_collection = db["resumes"]
skills_collection = db["skills"]
skill_categories_collection = db["skill_categories"]
user_skills_collection = db["user_skills"]
companies_collection = db["companies"]
jobs_collection = db["jobs"]
job_skills_collection = db["job_skills"]
applications_collection = db["applications"]
saved_jobs_collection = db["saved_jobs"]
interviews_collection = db["interviews"]
videos_collection = db["videos"]
video_likes_collection = db["video_likes"]
video_comments_collection = db["video_comments"]
video_saves_collection = db["video_saves"]
followers_collection = db["followers"]
messages_collection = db["messages"]
notifications_collection = db["notifications"]
reports_collection = db["reports"]
banners_collection = db["banners"]
settings_collection = db["settings"]
password_resets_collection = db["password_resets"]
media_collection = db["media"]
site_content_collection = db["site_content"]
newsletter_collection = db["newsletter_subscribers"]
premium_requests_collection = db["premium_requests"]
prep_items_collection = db["prep_items"]
prep_progress_collection = db["prep_progress"]
assessments_collection = db["assessments"]
assessment_results_collection = db["assessment_results"]
resume_builds_collection = db["resume_builds"]
audit_logs_collection = db["audit_logs"]


def ensure_indexes() -> None:
    """Idempotent index creation (called on startup).

    A failing index (e.g. unique index over legacy duplicate rows) must never stop the API from
    booting, so each one is attempted separately and problems are only reported.
    """
    specs = [
        (users_collection, "email", {"unique": True}),
        (user_profiles_collection, "user_id", {"unique": True}),
        (skills_collection, "name", {"unique": True}),
        (user_skills_collection, [("user_id", ASCENDING), ("skill_id", ASCENDING)], {"unique": True}),
        (job_skills_collection, [("job_id", ASCENDING), ("skill_id", ASCENDING)], {"unique": True}),
        (applications_collection, [("job_id", ASCENDING), ("user_id", ASCENDING)], {"unique": True}),
        (saved_jobs_collection, [("user_id", ASCENDING), ("job_id", ASCENDING)], {"unique": True}),
        (video_likes_collection, [("video_id", ASCENDING), ("user_id", ASCENDING)], {"unique": True}),
        (video_saves_collection, [("video_id", ASCENDING), ("user_id", ASCENDING)], {"unique": True}),
        (followers_collection, [("follower_id", ASCENDING), ("following_id", ASCENDING)], {"unique": True}),
        (jobs_collection, [("created_at", DESCENDING)], {}),
        (jobs_collection, "recruiter_id", {}),
        (companies_collection, "recruiter_id", {}),
        (messages_collection, [("sender_id", ASCENDING), ("receiver_id", ASCENDING)], {}),
        (notifications_collection, [("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        (password_resets_collection, "token_hash", {}),
        (media_collection, "url", {}),
        (newsletter_collection, "email", {"unique": True}),
        (premium_requests_collection, "user_id", {}),
        (prep_progress_collection, [("user_id", ASCENDING), ("item_id", ASCENDING)], {"unique": True}),
        (assessment_results_collection, [("user_id", ASCENDING), ("assessment_id", ASCENDING)], {}),
        (resume_builds_collection, "user_id", {"unique": True}),
        (audit_logs_collection, [("created_at", DESCENDING)], {}),
    ]
    for collection, keys, opts in specs:
        try:
            collection.create_index(keys, **opts)
        except Exception as exc:  # pragma: no cover
            print(f"WARNING: could not create index on {collection.name} {keys}: {exc!r}")
