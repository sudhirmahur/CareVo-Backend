from bson import ObjectId

from fastapi import (
    APIRouter,
    Depends,
    HTTPException
)

from app.database import (
    skills_collection,
    user_skills_collection
)

from app.dependencies.auth import get_current_user

from app.models.skills import (
    create_user_skill_document
)

from app.schemas.skills import AddUserSkill


router = APIRouter(
    tags=["Skills"]
)


# -------------------------------
# GET ALL SKILLS
# -------------------------------

@router.get("")
def get_skills():

    skills = list(
        skills_collection.find({})
    )

    for skill in skills:

        skill["id"] = str(
            skill.pop("_id")
        )

        if skill.get("category_id"):

            skill["category_id"] = str(
                skill["category_id"]
            )

    return skills


# -------------------------------
# GET MY SKILLS
# -------------------------------

@router.get("/users/me/skills")
def get_my_skills(
    current_user=Depends(get_current_user)
):

    user_skills = list(
        user_skills_collection.find({
            "user_id": current_user["_id"]
        })
    )

    result = []

    for item in user_skills:

        skill = skills_collection.find_one({
            "_id": item["skill_id"]
        })

        if skill:

            result.append({
                "id": str(item["_id"]),
                "skill_id": str(skill["_id"]),
                "name": skill["name"],
                "proficiency": item["proficiency"]
            })

    return result


# -------------------------------
# ADD MY SKILL
# -------------------------------

@router.post("/users/me/skills")
def add_my_skill(
    data: AddUserSkill,
    current_user=Depends(get_current_user)
):

    if not ObjectId.is_valid(data.skill_id):

        raise HTTPException(
            status_code=400,
            detail="Invalid skill ID"
        )

    skill_id = ObjectId(data.skill_id)

    skill = skills_collection.find_one({
        "_id": skill_id
    })

    if not skill:

        raise HTTPException(
            status_code=404,
            detail="Skill not found"
        )

    existing = user_skills_collection.find_one({
        "user_id": current_user["_id"],
        "skill_id": skill_id
    })

    if existing:

        raise HTTPException(
            status_code=409,
            detail="Skill already added"
        )

    document = create_user_skill_document(
        user_id=current_user["_id"],
        skill_id=skill_id,
        proficiency=data.proficiency
    )

    result = user_skills_collection.insert_one(
        document
    )

    return {
        "message": "Skill added successfully",
        "id": str(result.inserted_id)
    }


# -------------------------------
# DELETE MY SKILL
# -------------------------------

@router.delete("/users/me/skills/{skill_id}")
def delete_my_skill(
    skill_id: str,
    current_user=Depends(get_current_user)
):

    if not ObjectId.is_valid(skill_id):

        raise HTTPException(
            status_code=400,
            detail="Invalid skill ID"
        )

    result = user_skills_collection.delete_one({
        "user_id": current_user["_id"],
        "skill_id": ObjectId(skill_id)
    })

    if result.deleted_count == 0:

        raise HTTPException(
            status_code=404,
            detail="User skill not found"
        )

    return {
        "message": "Skill removed successfully"
    }