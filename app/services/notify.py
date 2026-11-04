"""Create in-app notifications."""
from typing import Optional

from bson import ObjectId

from app.database import notifications_collection
from app.utils import now


def notify(
    user_id: ObjectId,
    title: str,
    message: str,
    type_: str = "system",
    link: Optional[str] = None,
) -> None:
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
