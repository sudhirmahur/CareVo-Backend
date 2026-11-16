"""Admin: career events & webinars."""
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.database import event_registrations_collection, events_collection, users_collection
from app.dependencies.auth import require_admin
from app.schemas.common import HttpUrl, MediaUrl
from app.services.audit import audit
from app.services.notify import notify
from app.utils import get_or_404, now, oid, paginate, rx, ser

router = APIRouter(dependencies=[Depends(require_admin)])
EventType = Literal["webinar", "workshop", "seminar", "career_fair", "networking"]


class EventIn(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    type: EventType = "webinar"
    description: str = Field(default="", max_length=3000)
    host: Optional[str] = Field(default=None, max_length=100)
    starts_at: datetime
    ends_at: Optional[datetime] = None
    location: Optional[str] = Field(default=None, max_length=150)
    join_url: HttpUrl = None
    image_url: MediaUrl = None
    capacity: Optional[int] = Field(default=None, ge=1, le=100000)
    is_published: bool = True

    @model_validator(mode="after")
    def _w(self):
        if self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("End time must be after the start time")
        return self


class EventUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=150)
    type: Optional[EventType] = None
    description: Optional[str] = Field(default=None, max_length=3000)
    host: Optional[str] = Field(default=None, max_length=100)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    location: Optional[str] = Field(default=None, max_length=150)
    join_url: HttpUrl = None
    image_url: MediaUrl = None
    capacity: Optional[int] = Field(default=None, ge=1, le=100000)
    is_published: Optional[bool] = None


def _row(e: dict) -> dict:
    out = ser(e)
    out["registrations"] = event_registrations_collection.count_documents({"event_id": e["_id"]})
    return out


@router.get("/events")
def admin_events(q: Optional[str] = Query(default=None, max_length=80), page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=100)):
    return paginate(events_collection, {"title": rx(q)} if q else {}, page, limit, [("starts_at", -1)], mapper=_row)


@router.post("/events", status_code=201)
def create_event(data: EventIn, admin=Depends(require_admin)):
    doc = {**data.model_dump(), "created_at": now(), "updated_at": now()}
    doc["_id"] = events_collection.insert_one(dict(doc)).inserted_id
    audit(admin, "event.create", str(doc["_id"]), data.title)
    return _row(doc)


@router.put("/events/{event_id}")
def update_event(event_id: str, data: EventUpdate, admin=Depends(require_admin)):
    e = get_or_404(events_collection, oid(event_id, "event ID"), "Event")
    v = data.model_dump(exclude_unset=True)
    if not v:
        raise HTTPException(400, "No data provided")
    s, en = v.get("starts_at", e["starts_at"]), v.get("ends_at", e.get("ends_at"))
    if s and en:
        tz = now().tzinfo
        if (en if en.tzinfo else en.replace(tzinfo=tz)) <= (s if s.tzinfo else s.replace(tzinfo=tz)):
            raise HTTPException(422, "End time must be after the start time")
    v["updated_at"] = now()
    events_collection.update_one({"_id": e["_id"]}, {"$set": v})
    if "starts_at" in v and v["starts_at"] != e["starts_at"]:
        for r in event_registrations_collection.find({"event_id": e["_id"]}):
            notify(r["user_id"], f"Time changed: {e['title']}", "The organiser moved this event. Please check the new time.", "system", "/events")
            event_registrations_collection.update_one({"_id": r["_id"]}, {"$set": {"reminded": False}})
    audit(admin, "event.update", str(e["_id"]))
    return _row(events_collection.find_one({"_id": e["_id"]}))


@router.delete("/events/{event_id}")
def delete_event(event_id: str, admin=Depends(require_admin)):
    e = get_or_404(events_collection, oid(event_id, "event ID"), "Event")
    for r in event_registrations_collection.find({"event_id": e["_id"]}):
        notify(r["user_id"], f"Cancelled: {e['title']}", "This event was cancelled.", "system", "/events")
    event_registrations_collection.delete_many({"event_id": e["_id"]})
    events_collection.delete_one({"_id": e["_id"]})
    audit(admin, "event.delete", str(e["_id"]), e["title"])
    return {"message": "Event deleted"}


@router.get("/events/{event_id}/registrations")
def registrations(event_id: str):
    e = get_or_404(events_collection, oid(event_id, "event ID"), "Event")
    rows = list(event_registrations_collection.find({"event_id": e["_id"]}).sort("created_at", 1))
    users = {u["_id"]: u for u in users_collection.find({"_id": {"$in": [r["user_id"] for r in rows]}}, {"name": 1, "email": 1})}
    return {"items": [{"name": users[r["user_id"]]["name"], "email": users[r["user_id"]]["email"], "registered_at": ser(r["created_at"])} for r in rows if r["user_id"] in users], "total": len(rows)}
