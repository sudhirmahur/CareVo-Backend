"""1-to-1 chat between candidates, recruiters and admins (REST + polling)."""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import companies_collection, messages_collection, users_collection
from app.dependencies.auth import get_current_user
from app.schemas.social import MessageIn
from app.services.notify import notify
from app.utils import now, oid, ser

router = APIRouter()


def _peer(u: dict) -> dict:
    company = companies_collection.find_one({"recruiter_id": u["_id"]}, {"name": 1}) if u["role"] == "recruiter" else None
    return {
        "id": str(u["_id"]),
        "name": u["name"],
        "role": u["role"],
        "profile_image": u.get("profile_image"),
        "company_name": company["name"] if company else None,
    }


@router.get("/conversations")
def conversations(user=Depends(get_current_user)):
    uid = user["_id"]
    rows = list(
        messages_collection.find({"$or": [{"sender_id": uid}, {"receiver_id": uid}]}).sort("created_at", -1)
    )
    threads: dict = {}
    for m in rows:
        other = m["receiver_id"] if m["sender_id"] == uid else m["sender_id"]
        t = threads.setdefault(other, {"last": m, "unread": 0})
        if m["receiver_id"] == uid and not m.get("is_read"):
            t["unread"] += 1
    people = {u["_id"]: u for u in users_collection.find({"_id": {"$in": list(threads)}})}
    out = []
    for other, t in threads.items():
        if other not in people:
            continue
        out.append(
            {
                "peer": _peer(people[other]),
                "last_message": t["last"]["message"] or ("Attachment: " + t["last"].get("attachment", {}).get("kind", "file")),
                "last_at": ser(t["last"]["created_at"]),
                "last_from_me": t["last"]["sender_id"] == uid,
                "unread": t["unread"],
            }
        )
    out.sort(key=lambda x: x["last_at"], reverse=True)
    return {"items": out, "unread_total": sum(x["unread"] for x in out)}


@router.get("/unread-count")
def unread_count(user=Depends(get_current_user)):
    return {"count": messages_collection.count_documents({"receiver_id": user["_id"], "is_read": False})}


@router.get("/peer/{peer_id}")
def peer_info(peer_id: str, user=Depends(get_current_user)):
    peer = users_collection.find_one({"_id": oid(peer_id, "user ID"), "is_active": True})
    if not peer:
        raise HTTPException(404, "User not found")
    return _peer(peer)


@router.get("/with/{peer_id}")
def thread(peer_id: str, limit: int = Query(default=100, ge=1, le=300), user=Depends(get_current_user)):
    pid = oid(peer_id, "user ID")
    uid = user["_id"]
    rows = list(
        messages_collection.find(
            {"$or": [{"sender_id": uid, "receiver_id": pid}, {"sender_id": pid, "receiver_id": uid}]}
        ).sort("created_at", -1).limit(limit)
    )
    rows.reverse()
    messages_collection.update_many({"sender_id": pid, "receiver_id": uid, "is_read": False}, {"$set": {"is_read": True}})
    return {"items": [ser(m) for m in rows]}


@router.post("", status_code=201)
def send(data: MessageIn, user=Depends(get_current_user)):
    rid = oid(data.receiver_id, "receiver ID")
    if rid == user["_id"]:
        raise HTTPException(400, "You cannot message yourself")
    receiver = users_collection.find_one({"_id": rid, "is_active": True})
    if not receiver:
        raise HTTPException(404, "Recipient not found")
    text = data.message.strip()
    doc = {"sender_id": user["_id"], "receiver_id": rid, "message": text, "is_read": False, "created_at": now()}
    if data.attachment:
        doc["attachment"] = data.attachment.model_dump()
    doc["_id"] = messages_collection.insert_one(dict(doc)).inserted_id
    notify(rid, f"New message from {user['name']}", (text or f"Sent you a {data.attachment.kind}")[:120], "message", "/messages")
    return ser(doc)
