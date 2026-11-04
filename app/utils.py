"""Small shared helpers: ids, serialisation, pagination, regex."""
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from bson import ObjectId
from fastapi import HTTPException

HIDDEN_FIELDS = {"password_hash"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def oid(value: Any, label: str = "ID") -> ObjectId:
    """Convert to ObjectId or raise a clean 400."""
    if isinstance(value, ObjectId):
        return value
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return ObjectId(value)


def ser(obj: Any) -> Any:
    """Make a Mongo document JSON-safe: ObjectId->str, _id->id, datetime->ISO."""
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.isoformat()
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in HIDDEN_FIELDS:
                continue
            out["id" if key == "_id" else key] = ser(value)
        return out
    if isinstance(obj, (list, tuple, set)):
        return [ser(v) for v in obj]
    return obj


def rx(text: str) -> dict:
    """Case-insensitive, injection-safe 'contains' regex."""
    return {"$regex": re.escape(text.strip()), "$options": "i"}


def paginate(
    collection,
    query: dict,
    page: int = 1,
    limit: int = 20,
    sort: Optional[list] = None,
    mapper: Optional[Callable[[dict], dict]] = None,
    max_limit: int = 100,
) -> dict:
    page = max(1, int(page or 1))
    limit = min(max(1, int(limit or 20)), max_limit)
    total = collection.count_documents(query)
    cursor = collection.find(query)
    if sort:
        cursor = cursor.sort(sort)
    docs = list(cursor.skip((page - 1) * limit).limit(limit))
    items = [mapper(d) if mapper else ser(d) for d in docs]
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
    }


def clean_list(values: Optional[list]) -> list:
    """Trim, drop blanks and de-duplicate (case-insensitive) while keeping order."""
    seen, out = set(), []
    for v in values or []:
        s = str(v).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def get_or_404(collection, _id: ObjectId, label: str = "Item", extra: Optional[dict] = None) -> dict:
    query = {"_id": _id}
    if extra:
        query.update(extra)
    doc = collection.find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return doc
