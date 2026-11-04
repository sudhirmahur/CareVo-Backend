"""Candidate-side application history."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import applications_collection, jobs_collection
from app.dependencies.auth import require_roles
from app.services.applications import shape_applications
from app.utils import get_or_404, now, oid

router = APIRouter()


@router.get("")
def my_applications(
    status: Optional[str] = Query(default=None, pattern="^(applied|shortlisted|rejected|accepted|withdrawn)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user=Depends(require_roles("user")),
):
    query: dict = {"user_id": user["_id"]}
    if status:
        query["status"] = status
    total = applications_collection.count_documents(query)
    docs = list(
        applications_collection.find(query).sort("applied_at", -1).skip((page - 1) * limit).limit(limit)
    )
    return {
        "items": shape_applications(docs, "user"),
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
    }


@router.get("/{application_id}")
def get_application(application_id: str, user=Depends(require_roles("user"))):
    app = get_or_404(
        applications_collection, oid(application_id, "application ID"), "Application", {"user_id": user["_id"]}
    )
    return shape_applications([app], "user")[0]


@router.post("/{application_id}/withdraw")
def withdraw(application_id: str, user=Depends(require_roles("user"))):
    app = get_or_404(
        applications_collection, oid(application_id, "application ID"), "Application", {"user_id": user["_id"]}
    )
    if app["status"] in ("accepted", "rejected", "withdrawn"):
        raise HTTPException(400, f"Cannot withdraw an application that is {app['status']}")
    ts = now()
    applications_collection.update_one(
        {"_id": app["_id"]},
        {
            "$set": {"status": "withdrawn", "updated_at": ts},
            "$push": {"status_history": {"status": "withdrawn", "at": ts, "note": "Withdrawn by candidate"}},
        },
    )
    jobs_collection.update_one({"_id": app["job_id"], "applications_count": {"$gt": 0}}, {"$inc": {"applications_count": -1}})
    return {"message": "Application withdrawn", "status": "withdrawn"}
