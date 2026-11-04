from fastapi import APIRouter, Depends, Query

from app.database import notifications_collection
from app.dependencies.auth import get_current_user
from app.utils import get_or_404, oid, paginate

router = APIRouter()


@router.get("")
def list_notifications(
    unread: bool = False,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user=Depends(get_current_user),
):
    query: dict = {"user_id": user["_id"]}
    if unread:
        query["is_read"] = False
    result = paginate(notifications_collection, query, page, limit, [("created_at", -1)])
    result["unread_count"] = notifications_collection.count_documents({"user_id": user["_id"], "is_read": False})
    return result


@router.get("/unread-count")
def unread_count(user=Depends(get_current_user)):
    return {"count": notifications_collection.count_documents({"user_id": user["_id"], "is_read": False})}


@router.post("/read-all")
def read_all(user=Depends(get_current_user)):
    res = notifications_collection.update_many({"user_id": user["_id"], "is_read": False}, {"$set": {"is_read": True}})
    return {"message": "All notifications marked as read", "updated": res.modified_count}


@router.patch("/{notification_id}/read")
def mark_read(notification_id: str, user=Depends(get_current_user)):
    n = get_or_404(notifications_collection, oid(notification_id, "notification ID"), "Notification", {"user_id": user["_id"]})
    notifications_collection.update_one({"_id": n["_id"]}, {"$set": {"is_read": True}})
    return {"message": "Marked as read"}


@router.delete("/{notification_id}")
def delete_notification(notification_id: str, user=Depends(get_current_user)):
    n = get_or_404(notifications_collection, oid(notification_id, "notification ID"), "Notification", {"user_id": user["_id"]})
    notifications_collection.delete_one({"_id": n["_id"]})
    return {"message": "Notification deleted"}
