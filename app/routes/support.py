"""Help & support tickets and product feedback (candidates + recruiters) with the admin desk."""
from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import counters_collection, feedback_collection, tickets_collection, users_collection
from app.dependencies.auth import get_current_user, require_admin
from app.schemas.support import AdminTicketUpdate, FeedbackIn, FeedbackUpdate, NoteIn, ReplyIn, TicketIn
from app.services.audit import audit
from app.services.notify import notify
from app.utils import get_or_404, now, oid, paginate, rx, ser

router = APIRouter()
admin_router = APIRouter(dependencies=[Depends(require_admin)])


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _next_no() -> str:
    c = counters_collection.find_one_and_update({"_id": "ticket"}, {"$inc": {"seq": 1}}, upsert=True, return_document=True)
    return f"CV-{int(c['seq']):06d}"


def _who(uid) -> Optional[dict]:
    u = users_collection.find_one({"_id": uid}, {"name": 1, "email": 1, "role": 1})
    return {"id": str(u["_id"]), "name": u["name"], "email": u["email"], "role": u["role"]} if u else None


def _ticket_out(t: dict, admin: bool = False) -> dict:
    out = ser(t)
    out["user"] = _who(t["user_id"])
    out["assignee"] = _who(t["assigned_to"]) if t.get("assigned_to") else None
    out["messages_count"] = len(t.get("messages", []))
    if t.get("resolved_at"):
        out["resolution_hours"] = round((_aware(t["resolved_at"]) - _aware(t["created_at"])).total_seconds() / 3600, 2)
    msgs = []
    for m in t.get("messages", []):
        item = ser(m)
        item["author"] = _who(m["author_id"])
        msgs.append(item)
    out["messages"] = msgs
    if admin:
        out["notes"] = [{**ser(n), "author": _who(n["author_id"])} for n in t.get("notes", [])]
    else:
        out.pop("notes", None)
    out.pop("assigned_to", None) if not admin else None
    return out


def _summary(t: dict) -> dict:
    o = _ticket_out(t)
    o.pop("messages", None)
    return o


# ------------------------------------------------------------------ user side
@router.post("/support/tickets", status_code=201)
def create_ticket(data: TicketIn, user=Depends(get_current_user)):
    ts = now()
    doc = {
        "ticket_no": _next_no(), "user_id": user["_id"], "subject": data.subject.strip(), "category": data.category, "priority": data.priority, "status": "open", "assigned_to": None,
        "messages": [{"author_id": user["_id"], "staff": False, "message": data.message.strip(), "created_at": ts}], "notes": [], "created_at": ts, "updated_at": ts, "resolved_at": None,
    }
    doc["_id"] = tickets_collection.insert_one(dict(doc)).inserted_id
    for a in users_collection.find({"role": "admin", "is_active": True}, {"_id": 1}):
        notify(a["_id"], f"New ticket {doc['ticket_no']}", doc["subject"][:100], "system", "/admin/support")
    return _ticket_out(doc)


@router.get("/support/tickets")
def my_tickets(status: Optional[str] = Query(default=None, pattern="^(open|in_progress|waiting|resolved|closed)$"), page: int = Query(1, ge=1), limit: int = Query(10, ge=1, le=50), user=Depends(get_current_user)):
    q: dict = {"user_id": user["_id"]}
    if status:
        q["status"] = status
    return paginate(tickets_collection, q, page, limit, [("updated_at", -1)], mapper=_summary)


@router.get("/support/tickets/{ticket_id}")
def my_ticket(ticket_id: str, user=Depends(get_current_user)):
    return _ticket_out(get_or_404(tickets_collection, oid(ticket_id, "ticket ID"), "Ticket", {"user_id": user["_id"]}))


@router.post("/support/tickets/{ticket_id}/reply")
def reply_ticket(ticket_id: str, data: ReplyIn, user=Depends(get_current_user)):
    t = get_or_404(tickets_collection, oid(ticket_id, "ticket ID"), "Ticket", {"user_id": user["_id"]})
    if t["status"] == "closed":
        raise HTTPException(400, "This ticket is closed. Please open a new one.")
    new_status = "open" if t["status"] in ("waiting", "resolved") else t["status"]
    tickets_collection.update_one({"_id": t["_id"]}, {"$push": {"messages": {"author_id": user["_id"], "staff": False, "message": data.message.strip(), "created_at": now()}}, "$set": {"status": new_status, "updated_at": now(), "resolved_at": None}})
    if t.get("assigned_to"):
        notify(t["assigned_to"], f"Reply on {t['ticket_no']}", data.message[:100], "system", "/admin/support")
    return _ticket_out(tickets_collection.find_one({"_id": t["_id"]}))


@router.post("/support/tickets/{ticket_id}/close")
def close_my_ticket(ticket_id: str, user=Depends(get_current_user)):
    t = get_or_404(tickets_collection, oid(ticket_id, "ticket ID"), "Ticket", {"user_id": user["_id"]})
    tickets_collection.update_one({"_id": t["_id"]}, {"$set": {"status": "closed", "updated_at": now(), "resolved_at": t.get("resolved_at") or now()}})
    return _ticket_out(tickets_collection.find_one({"_id": t["_id"]}))


# ------------------------------------------------------------------ feedback
@router.post("/feedback", status_code=201)
def send_feedback(data: FeedbackIn, user=Depends(get_current_user)):
    doc = {"user_id": user["_id"], "role": user["role"], "category": data.category, "rating": data.rating, "message": data.message.strip(), "status": "new", "response": None, "created_at": now(), "updated_at": now()}
    doc["_id"] = feedback_collection.insert_one(dict(doc)).inserted_id
    return {"message": "Thank you for your feedback!", "id": str(doc["_id"])}


@router.get("/feedback/mine")
def my_feedback(user=Depends(get_current_user)):
    return {"items": [ser(f) for f in feedback_collection.find({"user_id": user["_id"]}).sort("created_at", -1).limit(30)]}


# ------------------------------------------------------------------ admin desk
@admin_router.get("/support/tickets")
def admin_tickets(
    status: Optional[str] = Query(default=None, pattern="^(open|in_progress|waiting|resolved|closed)$"), priority: Optional[str] = Query(default=None, pattern="^(low|normal|high|urgent)$"),
    category: Optional[str] = None, assigned: Optional[str] = Query(default=None, max_length=40), q: Optional[str] = Query(default=None, max_length=80), page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=100),
):
    query: dict = {}
    if status:
        query["status"] = status
    if priority:
        query["priority"] = priority
    if category:
        query["category"] = category
    if assigned == "unassigned":
        query["assigned_to"] = None
    elif assigned:
        query["assigned_to"] = oid(assigned, "admin ID")
    if q:
        ids = [u["_id"] for u in users_collection.find({"$or": [{"email": rx(q)}, {"name": rx(q)}]}, {"_id": 1}).limit(100)]
        query["$or"] = [{"ticket_no": rx(q)}, {"subject": rx(q)}, {"user_id": {"$in": ids}}]
    return paginate(tickets_collection, query, page, limit, [("updated_at", -1)], mapper=lambda t: {**_summary(t), "assignee": _who(t["assigned_to"]) if t.get("assigned_to") else None})


@admin_router.get("/support/summary")
def ticket_summary():
    by_status = {s: tickets_collection.count_documents({"status": s}) for s in ("open", "in_progress", "waiting", "resolved", "closed")}
    resolved = list(tickets_collection.find({"resolved_at": {"$ne": None}}, {"created_at": 1, "resolved_at": 1, "category": 1, "assigned_to": 1}))
    hours = [(_aware(t["resolved_at"]) - _aware(t["created_at"])).total_seconds() / 3600 for t in resolved]
    by_cat: dict = {}
    for t in tickets_collection.find({}, {"category": 1}):
        by_cat[t["category"]] = by_cat.get(t["category"], 0) + 1
    agents: dict = {}
    for t in resolved:
        if t.get("assigned_to"):
            agents[str(t["assigned_to"])] = agents.get(str(t["assigned_to"]), 0) + 1
    return {
        "total": sum(by_status.values()), "by_status": by_status, "by_category": by_cat, "unassigned": tickets_collection.count_documents({"assigned_to": None, "status": {"$in": ["open", "in_progress"]}}),
        "avg_resolution_hours": round(sum(hours) / len(hours), 2) if hours else None,
        "resolved_by_agent": [{"admin": _who(__import__("bson").ObjectId(k)), "resolved": v} for k, v in agents.items()],
    }


@admin_router.get("/support/agents")
def agents():
    return {"items": [{"id": str(u["_id"]), "name": u["name"], "email": u["email"]} for u in users_collection.find({"role": "admin", "is_active": True}, {"name": 1, "email": 1})]}


@admin_router.get("/support/tickets/{ticket_id}")
def admin_ticket(ticket_id: str):
    return _ticket_out(get_or_404(tickets_collection, oid(ticket_id, "ticket ID"), "Ticket"), admin=True)


@admin_router.post("/support/tickets/{ticket_id}/reply")
def admin_reply(ticket_id: str, data: ReplyIn, admin=Depends(require_admin)):
    t = get_or_404(tickets_collection, oid(ticket_id, "ticket ID"), "Ticket")
    if t["status"] == "closed":
        raise HTTPException(400, "Reopen the ticket before replying")
    sets = {"status": "waiting", "updated_at": now()}
    if not t.get("assigned_to"):
        sets["assigned_to"] = admin["_id"]
    tickets_collection.update_one({"_id": t["_id"]}, {"$push": {"messages": {"author_id": admin["_id"], "staff": True, "message": data.message.strip(), "created_at": now()}}, "$set": sets})
    notify(t["user_id"], f"Support replied: {t['ticket_no']}", data.message[:100], "system", "/support")
    return _ticket_out(tickets_collection.find_one({"_id": t["_id"]}), admin=True)


@admin_router.post("/support/tickets/{ticket_id}/notes")
def admin_note(ticket_id: str, data: NoteIn, admin=Depends(require_admin)):
    t = get_or_404(tickets_collection, oid(ticket_id, "ticket ID"), "Ticket")
    tickets_collection.update_one({"_id": t["_id"]}, {"$push": {"notes": {"author_id": admin["_id"], "note": data.note.strip(), "created_at": now()}}})
    return _ticket_out(tickets_collection.find_one({"_id": t["_id"]}), admin=True)


@admin_router.patch("/support/tickets/{ticket_id}")
def admin_update(ticket_id: str, data: AdminTicketUpdate, admin=Depends(require_admin)):
    t = get_or_404(tickets_collection, oid(ticket_id, "ticket ID"), "Ticket")
    sets: dict = {"updated_at": now()}
    v = data.model_dump(exclude_unset=True)
    if "priority" in v and v["priority"]:
        sets["priority"] = v["priority"]
    if "assigned_to" in v:
        if v["assigned_to"]:
            a = get_or_404(users_collection, oid(v["assigned_to"], "admin ID"), "Admin", {"role": "admin", "is_active": True})
            sets["assigned_to"] = a["_id"]
            if a["_id"] != admin["_id"]:
                notify(a["_id"], f"Ticket assigned: {t['ticket_no']}", t["subject"][:100], "system", "/admin/support")
        else:
            sets["assigned_to"] = None
    if "status" in v and v["status"]:
        sets["status"] = v["status"]
        if v["status"] in ("resolved", "closed"):
            sets["resolved_at"] = t.get("resolved_at") or now()
        else:
            sets["resolved_at"] = None
        if v["status"] != t["status"]:
            notify(t["user_id"], f"Ticket {t['ticket_no']} is {v['status'].replace('_', ' ')}", t["subject"][:100], "system", "/support")
    tickets_collection.update_one({"_id": t["_id"]}, {"$set": sets})
    audit(admin, "ticket.update", t["ticket_no"], ", ".join(f"{k}={v}" for k, v in v.items()))
    return _ticket_out(tickets_collection.find_one({"_id": t["_id"]}), admin=True)


@admin_router.get("/feedback")
def admin_feedback(category: Optional[str] = None, status: Optional[str] = None, role: Optional[str] = Query(default=None, pattern="^(user|recruiter)$"), page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=100)):
    q: dict = {}
    for k, v in (("category", category), ("status", status), ("role", role)):
        if v:
            q[k] = v
    return paginate(feedback_collection, q, page, limit, [("created_at", -1)], mapper=lambda f: {**ser(f), "user": _who(f["user_id"])})


@admin_router.get("/feedback/summary")
def feedback_summary():
    rows = list(feedback_collection.find({}))
    rated = [r["rating"] for r in rows if r.get("rating")]
    cnt = lambda key: {k: sum(1 for r in rows if r.get(key) == k) for k in sorted({r.get(key) for r in rows if r.get(key)})}
    return {
        "total": len(rows), "average_rating": round(sum(rated) / len(rated), 2) if rated else None, "ratings_count": len(rated), "by_category": cnt("category"), "by_status": cnt("status"), "by_role": cnt("role"),
        "rating_distribution": {str(i): sum(1 for x in rated if x == i) for i in range(1, 6)},
    }


@admin_router.patch("/feedback/{feedback_id}")
def admin_feedback_update(feedback_id: str, data: FeedbackUpdate, admin=Depends(require_admin)):
    f = get_or_404(feedback_collection, oid(feedback_id, "feedback ID"), "Feedback")
    v = data.model_dump(exclude_unset=True)
    if not v:
        raise HTTPException(400, "No data provided")
    v["updated_at"] = now()
    feedback_collection.update_one({"_id": f["_id"]}, {"$set": v})
    if v.get("response") and v["response"] != f.get("response"):
        notify(f["user_id"], "We replied to your feedback", v["response"][:120], "system")
    audit(admin, "feedback.update", str(f["_id"]), v.get("status"))
    return {**ser(feedback_collection.find_one({"_id": f["_id"]})), "user": _who(f["user_id"])}


@admin_router.get("/feedback/export")
def feedback_export():
    import csv
    import io

    from fastapi.responses import Response

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "role", "name", "category", "rating", "status", "message", "response"])
    for f in feedback_collection.find({}).sort("created_at", -1):
        u = _who(f["user_id"]) or {}
        w.writerow([ser(f["created_at"])[:19], f["role"], u.get("name", ""), f["category"], f.get("rating") or "", f["status"], f["message"], f.get("response") or ""])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=feedback.csv"})
