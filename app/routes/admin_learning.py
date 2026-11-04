"""Admin CRUD for interview-prep questions and skill assessments."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import assessment_results_collection, assessments_collection, prep_items_collection, prep_progress_collection
from app.dependencies.auth import require_admin
from app.schemas.learning import AssessmentIn, AssessmentUpdate, PrepIn, PrepUpdate
from app.services.audit import audit
from app.utils import get_or_404, now, oid, paginate, rx, ser

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/prep")
def prep_list(q: Optional[str] = Query(default=None, max_length=100), category: Optional[str] = None, page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=100)):
    query: dict = {}
    if q:
        query["$or"] = [{"question": rx(q)}, {"category": rx(q)}]
    if category:
        query["category"] = category
    return paginate(prep_items_collection, query, page, limit, [("category", 1), ("created_at", 1)])


@router.post("/prep", status_code=201)
def prep_create(data: PrepIn, admin=Depends(require_admin)):
    ts = now()
    doc = {**data.model_dump(), "created_at": ts, "updated_at": ts}
    doc["_id"] = prep_items_collection.insert_one(dict(doc)).inserted_id
    audit(admin, "prep.create", str(doc["_id"]))
    return ser(doc)


@router.put("/prep/{item_id}")
def prep_update(item_id: str, data: PrepUpdate, admin=Depends(require_admin)):
    item = get_or_404(prep_items_collection, oid(item_id, "question ID"), "Question")
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No data provided")
    values["updated_at"] = now()
    prep_items_collection.update_one({"_id": item["_id"]}, {"$set": values})
    return ser(prep_items_collection.find_one({"_id": item["_id"]}))


@router.delete("/prep/{item_id}")
def prep_delete(item_id: str, admin=Depends(require_admin)):
    item = get_or_404(prep_items_collection, oid(item_id, "question ID"), "Question")
    prep_items_collection.delete_one({"_id": item["_id"]})
    prep_progress_collection.delete_many({"item_id": item["_id"]})
    audit(admin, "prep.delete", str(item["_id"]))
    return {"message": "Deleted"}


@router.get("/assessments")
def assessments_list():
    rows = list(assessments_collection.find({}).sort("created_at", 1))
    out = []
    for a in rows:
        item = ser(a)
        item["questions_count"] = len(a["questions"])
        item["attempts"] = assessment_results_collection.count_documents({"assessment_id": a["_id"]})
        out.append(item)
    return {"items": out, "total": len(out)}


@router.post("/assessments", status_code=201)
def assessment_create(data: AssessmentIn, admin=Depends(require_admin)):
    ts = now()
    doc = {**data.model_dump(), "created_at": ts, "updated_at": ts}
    doc["_id"] = assessments_collection.insert_one(dict(doc)).inserted_id
    audit(admin, "assessment.create", str(doc["_id"]), doc["title"])
    return ser(doc)


@router.put("/assessments/{assessment_id}")
def assessment_update(assessment_id: str, data: AssessmentUpdate, admin=Depends(require_admin)):
    a = get_or_404(assessments_collection, oid(assessment_id, "assessment ID"), "Assessment")
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No data provided")
    values["updated_at"] = now()
    assessments_collection.update_one({"_id": a["_id"]}, {"$set": values})
    return ser(assessments_collection.find_one({"_id": a["_id"]}))


@router.delete("/assessments/{assessment_id}")
def assessment_delete(assessment_id: str, admin=Depends(require_admin)):
    a = get_or_404(assessments_collection, oid(assessment_id, "assessment ID"), "Assessment")
    assessments_collection.delete_one({"_id": a["_id"]})
    assessment_results_collection.delete_many({"assessment_id": a["_id"]})
    audit(admin, "assessment.delete", str(a["_id"]), a["title"])
    return {"message": "Deleted"}
