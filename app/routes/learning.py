"""Candidate learning tools: interview prep, skill assessments, resume builder."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import (
    assessment_results_collection,
    assessments_collection,
    prep_items_collection,
    prep_progress_collection,
    resume_builds_collection,
    user_profiles_collection,
)
from app.dependencies.auth import get_current_user
from app.schemas.learning import PrepProgress, ResumeBuild, Submission
from app.utils import get_or_404, now, oid, paginate, rx, ser

router = APIRouter()


# ------------------------------------------------------------- interview prep
@router.get("/prep/categories")
def prep_categories(user=Depends(get_current_user)):
    cats = sorted(c for c in prep_items_collection.distinct("category", {"is_active": True}))
    return {"items": cats}


@router.get("/prep")
def prep_list(
    category: Optional[str] = None,
    difficulty: Optional[str] = Query(default=None, pattern="^(easy|medium|hard)$"),
    q: Optional[str] = Query(default=None, max_length=100),
    status: Optional[str] = Query(default=None, pattern="^(known|review|none)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=12, ge=1, le=50),
    user=Depends(get_current_user),
):
    query: dict = {"is_active": True}
    if category:
        query["category"] = category
    if difficulty:
        query["difficulty"] = difficulty
    if q:
        query["$or"] = [{"question": rx(q)}, {"answer": rx(q)}]
    if status:
        marks = {p["item_id"]: p["status"] for p in prep_progress_collection.find({"user_id": user["_id"]})}
        if status == "none":
            query["_id"] = {"$nin": list(marks)}
        else:
            query["_id"] = {"$in": [k for k, v in marks.items() if v == status]}
    result = paginate(prep_items_collection, query, page, limit, [("category", 1), ("created_at", 1)], mapper=lambda d: d)
    marks = {p["item_id"]: p["status"] for p in prep_progress_collection.find({"user_id": user["_id"], "item_id": {"$in": [d["_id"] for d in result["items"]]}})}
    result["items"] = [{**ser(d), "my_status": marks.get(d["_id"], "none")} for d in result["items"]]
    all_marks = list(prep_progress_collection.find({"user_id": user["_id"]}))
    result["progress"] = {
        "known": sum(1 for m in all_marks if m["status"] == "known"),
        "review": sum(1 for m in all_marks if m["status"] == "review"),
        "total": prep_items_collection.count_documents({"is_active": True}),
    }
    return result


@router.put("/prep/{item_id}/progress")
def prep_progress(item_id: str, data: PrepProgress, user=Depends(get_current_user)):
    item = get_or_404(prep_items_collection, oid(item_id, "question ID"), "Question", {"is_active": True})
    if data.status == "none":
        prep_progress_collection.delete_one({"user_id": user["_id"], "item_id": item["_id"]})
    else:
        prep_progress_collection.update_one(
            {"user_id": user["_id"], "item_id": item["_id"]},
            {"$set": {"status": data.status, "updated_at": now()}},
            upsert=True,
        )
    return {"status": data.status}


# ------------------------------------------------------------- assessments
def _public_assessment(a: dict, best: Optional[dict], with_questions: bool = False) -> dict:
    item = {
        "id": str(a["_id"]), "title": a["title"], "skill": a["skill"], "description": a.get("description", ""),
        "pass_percent": a.get("pass_percent", 60), "questions_count": len(a["questions"]),
        "best": {"percent": best["percent"], "passed": best["passed"], "at": ser(best["created_at"])} if best else None,
    }
    if with_questions:  # never include the answers or explanations
        item["questions"] = [{"q": q["q"], "options": q["options"]} for q in a["questions"]]
    return item


def _best(uid, aid) -> Optional[dict]:
    rows = list(assessment_results_collection.find({"user_id": uid, "assessment_id": aid}).sort("percent", -1).limit(1))
    return rows[0] if rows else None


@router.get("/assessments")
def assessments_list(user=Depends(get_current_user)):
    rows = list(assessments_collection.find({"is_active": True}).sort("created_at", 1))
    return {"items": [_public_assessment(a, _best(user["_id"], a["_id"])) for a in rows]}


@router.get("/assessments/results/mine")
def my_results(user=Depends(get_current_user)):
    rows = list(assessment_results_collection.find({"user_id": user["_id"]}).sort("created_at", -1).limit(50))
    titles = {a["_id"]: a for a in assessments_collection.find({"_id": {"$in": [r["assessment_id"] for r in rows]}})}
    return {"items": [{**ser(r), "title": titles[r["assessment_id"]]["title"] if r["assessment_id"] in titles else "Removed assessment"} for r in rows]}


@router.get("/assessments/{assessment_id}")
def assessment_detail(assessment_id: str, user=Depends(get_current_user)):
    a = get_or_404(assessments_collection, oid(assessment_id, "assessment ID"), "Assessment", {"is_active": True})
    return _public_assessment(a, _best(user["_id"], a["_id"]), with_questions=True)


@router.post("/assessments/{assessment_id}/submit")
def assessment_submit(assessment_id: str, data: Submission, user=Depends(get_current_user)):
    a = get_or_404(assessments_collection, oid(assessment_id, "assessment ID"), "Assessment", {"is_active": True})
    qs = a["questions"]
    if len(data.answers) != len(qs):
        raise HTTPException(422, f"Please answer all {len(qs)} questions")
    review, score = [], 0
    for q, given in zip(qs, data.answers):
        correct = given is not None and 0 <= given < len(q["options"]) and given == q["answer"]
        score += 1 if correct else 0
        review.append({"q": q["q"], "options": q["options"], "your_answer": given, "correct_answer": q["answer"], "correct": correct, "explanation": q.get("explanation", "")})
    percent = round(score / len(qs) * 100)
    passed = percent >= a.get("pass_percent", 60)
    assessment_results_collection.insert_one(
        {"user_id": user["_id"], "assessment_id": a["_id"], "score": score, "total": len(qs), "percent": percent, "passed": passed, "created_at": now()}
    )
    if passed:  # a passed skill is shown as verified on the profile
        user_profiles_collection.update_one({"user_id": user["_id"]}, {"$addToSet": {"verified_skills": a["skill"]}})
    return {"score": score, "total": len(qs), "percent": percent, "passed": passed, "pass_percent": a.get("pass_percent", 60), "review": review}


# ------------------------------------------------------------- resume builder
@router.get("/resume-builder")
def builder_get(user=Depends(get_current_user)):
    row = resume_builds_collection.find_one({"user_id": user["_id"]})
    if row:
        return {"data": row["data"], "saved": True, "updated_at": ser(row["updated_at"])}
    profile = user_profiles_collection.find_one({"user_id": user["_id"]}) or {}
    links = [u for u in (profile.get("linkedin_url"), profile.get("github_url"), profile.get("portfolio_url")) if u]
    seed = ResumeBuild(
        full_name=user["name"], email=user["email"], phone=user.get("phone") or "", headline=profile.get("current_position", ""),
        location=profile.get("location", ""), summary=profile.get("bio", ""), skills=profile.get("skills", [])[:40], links=links,
    )
    return {"data": seed.model_dump(), "saved": False, "updated_at": None}


@router.put("/resume-builder")
def builder_save(data: ResumeBuild, user=Depends(get_current_user)):
    resume_builds_collection.update_one(
        {"user_id": user["_id"]}, {"$set": {"data": data.model_dump(), "updated_at": now()}, "$setOnInsert": {"created_at": now()}}, upsert=True
    )
    return {"message": "Resume saved"}
