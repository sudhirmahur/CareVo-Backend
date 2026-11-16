"""Banners and popups: admin management + public (scheduled, audience-aware) delivery."""
from fastapi import APIRouter, Depends, HTTPException

from app.database import banners_collection, popups_collection
from app.dependencies.auth import get_optional_user, require_admin
from app.schemas.announce import BannerIn, BannerUpdate, PopupIn, PopupUpdate
from app.services.audit import audit
from app.utils import get_or_404, now, oid, ser

admin_router = APIRouter(dependencies=[Depends(require_admin)])
public_router = APIRouter()


def _live_query(audience: str) -> dict:
    t = now()
    return {
        "is_active": True,
        "audience": {"$in": ["all", audience]},
        "$and": [
            {"$or": [{"starts_at": None}, {"starts_at": {"$exists": False}}, {"starts_at": {"$lte": t}}]},
            {"$or": [{"ends_at": None}, {"ends_at": {"$exists": False}}, {"ends_at": {"$gt": t}}]},
        ],
    }


def _audience_of(user) -> str:
    if not user:
        return "guest"
    return {"user": "user", "recruiter": "recruiter"}.get(user.get("role"), "all")


def _status(doc: dict) -> str:
    t = now()
    if not doc.get("is_active", True):
        return "hidden"
    s, e = doc.get("starts_at"), doc.get("ends_at")
    if s is not None and s.tzinfo is None:
        s = s.replace(tzinfo=t.tzinfo)
    if e is not None and e.tzinfo is None:
        e = e.replace(tzinfo=t.tzinfo)
    if s and s > t:
        return "scheduled"
    if e and e <= t:
        return "expired"
    return "live"


def _row(doc: dict) -> dict:
    out = ser(doc)
    out["status"] = _status(doc)
    return out


# ------------------------------------------------------------------ public
@public_router.get("/banners")
def public_banners(user=Depends(get_optional_user)):
    aud = _audience_of(user)
    q = _live_query(aud)
    return [ser(b) for b in banners_collection.find(q).sort("created_at", -1).limit(10)]


@public_router.get("/popups")
def public_popups(user=Depends(get_optional_user)):
    aud = _audience_of(user)
    q = _live_query(aud)
    return [ser(p) for p in popups_collection.find(q).sort("created_at", -1).limit(3)]


# ------------------------------------------------------------------ admin
def _crud(prefix: str, coll, label: str, In, Upd, audit_key: str):
    @admin_router.get(f"/{prefix}")
    def _list():
        return {"items": [_row(d) for d in coll.find({}).sort("created_at", -1)]}

    @admin_router.post(f"/{prefix}", status_code=201)
    def _create(data: In, admin=Depends(require_admin)):  # type: ignore[valid-type]
        doc = {**data.model_dump(), "created_at": now(), "updated_at": now()}
        doc["_id"] = coll.insert_one(dict(doc)).inserted_id
        audit(admin, f"{audit_key}.create", str(doc["_id"]), doc.get("title"))
        return _row(doc)

    @admin_router.put(f"/{prefix}/{{item_id}}")
    def _update(item_id: str, data: Upd, admin=Depends(require_admin)):  # type: ignore[valid-type]
        item = get_or_404(coll, oid(item_id, f"{label} ID"), label.capitalize())
        values = data.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(400, "No data provided")
        s = values.get("starts_at", item.get("starts_at"))
        e = values.get("ends_at", item.get("ends_at"))
        if s and e:
            s = s if s.tzinfo else s.replace(tzinfo=now().tzinfo)
            e = e if e.tzinfo else e.replace(tzinfo=now().tzinfo)
            if e <= s:
                raise HTTPException(422, "End time must be after the start time")
        values["updated_at"] = now()
        coll.update_one({"_id": item["_id"]}, {"$set": values})
        audit(admin, f"{audit_key}.update", str(item["_id"]))
        return _row(coll.find_one({"_id": item["_id"]}))

    @admin_router.patch(f"/{prefix}/{{item_id}}")
    def _toggle(item_id: str):
        item = get_or_404(coll, oid(item_id, f"{label} ID"), label.capitalize())
        coll.update_one({"_id": item["_id"]}, {"$set": {"is_active": not item.get("is_active", True)}})
        return _row(coll.find_one({"_id": item["_id"]}))

    @admin_router.delete(f"/{prefix}/{{item_id}}")
    def _delete(item_id: str, admin=Depends(require_admin)):
        item = get_or_404(coll, oid(item_id, f"{label} ID"), label.capitalize())
        coll.delete_one({"_id": item["_id"]})
        audit(admin, f"{audit_key}.delete", str(item["_id"]))
        return {"message": f"{label.capitalize()} deleted"}


_crud("banners", banners_collection, "banner", BannerIn, BannerUpdate, "banner")
_crud("popups", popups_collection, "popup", PopupIn, PopupUpdate, "popup")
