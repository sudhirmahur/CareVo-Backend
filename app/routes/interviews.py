"""Candidate view of their interviews."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.database import applications_collection, interviews_collection
from app.dependencies.auth import require_roles
from app.routes.recruiter import _shape_interviews

router = APIRouter()


@router.get("")
def my_interviews(
    status: Optional[str] = Query(default=None, pattern="^(scheduled|completed|cancelled)$"),
    user=Depends(require_roles("user")),
):
    query: dict = {"candidate_id": user["_id"]}
    if status:
        query["status"] = status
    rows = list(interviews_collection.find(query).sort("scheduled_at", 1))
    return {"items": _shape_interviews(rows), "total": len(rows)}
