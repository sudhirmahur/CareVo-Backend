"""Platform settings (single document, editable by admins)."""
from app.database import settings_collection
from app.utils import now

DEFAULTS = {
    "site_name": "Carevo",
    "support_email": "support@carevo.app",
    "allow_registration": True,
    "auto_approve_jobs": False,
    "auto_approve_companies": False,
    "maintenance_mode": False,
    "premium_gating": True,  # reels: Apply + recruiter profile need Premium for candidates
}


def get_settings() -> dict:
    doc = settings_collection.find_one({"key": "platform"}) or {}
    merged = {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS}}
    merged["updated_at"] = doc.get("updated_at")
    return merged


def update_settings(values: dict) -> dict:
    values = {k: v for k, v in values.items() if k in DEFAULTS}
    settings_collection.update_one(
        {"key": "platform"}, {"$set": {**values, "updated_at": now()}}, upsert=True
    )
    return get_settings()
