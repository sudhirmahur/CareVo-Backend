from fastapi import APIRouter, Depends, HTTPException

from app.database import (
    skill_categories_collection,
    skills_collection,
    user_skills_collection,
)
from app.dependencies.auth import get_current_user
from app.models.skills import create_user_skill_document
from app.schemas.social import AddUserSkill
from app.utils import oid, ser

router = APIRouter()


@router.get("")
def list_skills(category_id: str | None = None):
    query = {}
    if category_id:
        query["category_id"] = oid(category_id, "category ID")
    skills = list(skills_collection.find(query).sort("name", 1))
    return [ser(s) for s in skills]


@router.get("/categories")
def list_categories():
    return [ser(c) for c in skill_categories_collection.find({}).sort("name", 1)]


@router.get("/users/me/skills")
def my_skills(user=Depends(get_current_user)):
    rows = list(user_skills_collection.find({"user_id": user["_id"]}))
    skill_map = {
        s["_id"]: s for s in skills_collection.find({"_id": {"$in": [r["skill_id"] for r in rows]}})
    }
    out = []
    for r in rows:
        skill = skill_map.get(r["skill_id"])
        if skill:
            out.append(
                {
                    "id": str(r["_id"]),
                    "skill_id": str(skill["_id"]),
                    "name": skill["name"],
                    "proficiency": r["proficiency"],
                }
            )
    return out


@router.post("/users/me/skills", status_code=201)
def add_my_skill(data: AddUserSkill, user=Depends(get_current_user)):
    skill_id = oid(data.skill_id, "skill ID")
    if not skills_collection.find_one({"_id": skill_id}):
        raise HTTPException(404, "Skill not found")
    if user_skills_collection.find_one({"user_id": user["_id"], "skill_id": skill_id}):
        raise HTTPException(409, "Skill already added")
    result = user_skills_collection.insert_one(
        create_user_skill_document(user["_id"], skill_id, data.proficiency)
    )
    return {"message": "Skill added successfully", "id": str(result.inserted_id)}


@router.delete("/users/me/skills/{skill_id}")
def delete_my_skill(skill_id: str, user=Depends(get_current_user)):
    result = user_skills_collection.delete_one(
        {"user_id": user["_id"], "skill_id": oid(skill_id, "skill ID")}
    )
    if result.deleted_count == 0:
        raise HTTPException(404, "User skill not found")
    return {"message": "Skill removed successfully"}
