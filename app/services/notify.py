"""Create in-app notifications."""
from typing import Optional

from bson import ObjectId

from app.database import notifications_collection
from app.utils import now


# notification type -> preference category shown in Settings
CATEGORY = {"job": "jobs", "application": "applications", "interview": "interviews", "message": "messages", "system": "system", "promo": "promotions"}


def notify(
    user_id: ObjectId,
    title: str,
    message: str,
    type_: str = "system",
    link: Optional[str] = None,
) -> None:
    if type_ != "security":
        from app.database import users_collection

        u = users_collection.find_one({"_id": user_id}, {"notification_prefs": 1})
        prefs = (u or {}).get("notification_prefs") or {}
        cat = prefs.get("categories", {}).get(CATEGORY.get(type_, "system"), {})
        if cat.get("in_app") is False:
            return
    notifications_collection.insert_one(
        {
            "user_id": user_id,
            "title": title,
            "message": message,
            "type": type_,  # job | application | message | system
            "is_read": False,
            "link": link,
            "created_at": now(),
        }
    )
