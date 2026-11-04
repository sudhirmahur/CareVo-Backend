"""Append-only log of sensitive admin actions."""
from typing import Optional

from app.database import audit_logs_collection
from app.utils import now


def audit(admin: dict, action: str, target: str = "", detail: Optional[str] = None) -> None:
    try:
        audit_logs_collection.insert_one(
            {"admin_id": admin["_id"], "admin_email": admin.get("email"), "action": action, "target": target, "detail": (detail or "")[:300], "created_at": now()}
        )
    except Exception:  # pragma: no cover - logging must never break the request
        pass
