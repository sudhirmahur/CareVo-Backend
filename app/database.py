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
offer_assets_collection = db["offer_assets"]  # private signature images used on offer letters
answer_files_collection = db["answer_files"]  # files uploaded as answers to "file" screening questions
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
popups_collection = db["popups"]
plans_collection = db["plans"]
payments_collection = db["payments"]
subscriptions_collection = db["subscriptions"]
sessions_collection = db["sessions"]
activity_collection = db["activity"]
challenges_collection = db["auth_challenges"]
email_templates_collection = db["email_templates"]
email_outbox_collection = db["email_outbox"]
tickets_collection = db["tickets"]
counters_collection = db["counters"]
feedback_collection = db["feedback"]
blocks_collection = db["blocks"]
deletion_requests_collection = db["deletion_requests"]
admin_roles_collection = db["admin_roles"]
backups_collection = db["backups"]
health_checks_collection = db["health_checks"]
system_errors_collection = db["system_errors"]
events_collection = db["events"]
event_registrations_collection = db["event_registrations"]
documents_collection = db["documents"]
referrals_collection = db["referrals"]
referral_invites_collection = db["referral_invites"]
offers_collection = db["offers"]
offer_templates_collection = db["offer_templates"]
evaluations_collection = db["evaluations"]
calendar_events_collection = db["calendar_events"]
team_members_collection = db["team_members"]
candidate_pool_collection = db["candidate_pool"]
job_views_collection = db["job_views"]


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
        (payments_collection, "order_id", {"unique": True}),
        (admin_roles_collection, "key", {"unique": True}),
        (event_registrations_collection, [("event_id", ASCENDING), ("user_id", ASCENDING)], {"unique": True}),
        (documents_collection, [("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        (referrals_collection, "referred_id", {"unique": True}),
        (referrals_collection, "referrer_id", {}),
        (offers_collection, [("candidate_id", ASCENDING), ("created_at", DESCENDING)], {}),
        (offers_collection, "application_id", {}),
        (evaluations_collection, [("application_id", ASCENDING), ("created_at", DESCENDING)], {}),
        (calendar_events_collection, [("recruiter_id", ASCENDING), ("starts_at", ASCENDING)], {}),
        (team_members_collection, [("company_id", ASCENDING), ("email", ASCENDING)], {"unique": True}),
        (job_views_collection, [("job_id", ASCENDING), ("at", DESCENDING)], {}),
        (candidate_pool_collection, [("recruiter_id", ASCENDING), ("email", ASCENDING)], {"unique": True}),
        (sessions_collection, [("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        (activity_collection, [("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        (email_templates_collection, "key", {"unique": True}),
        (tickets_collection, "ticket_no", {"unique": True}),
        (tickets_collection, [("user_id", ASCENDING), ("updated_at", DESCENDING)], {}),
        (blocks_collection, [("blocker_id", ASCENDING), ("blocked_id", ASCENDING)], {"unique": True}),
        (payments_collection, [("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        (subscriptions_collection, [("user_id", ASCENDING), ("ends_at", DESCENDING)], {}),
    ]
    for collection, keys, opts in specs:
        try:
            collection.create_index(keys, **opts)
        except Exception as exc:  # pragma: no cover
            print(f"WARNING: could not create index on {collection.name} {keys}: {exc!r}")
