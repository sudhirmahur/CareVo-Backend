import os
import uuid

from datetime import datetime, timezone

from bson import ObjectId

from fastapi import (
    APIRouter,
    Depends,
    UploadFile,
    File,
    HTTPException
)

from app.database import (
    resumes_collection,
    user_profiles_collection
)

from app.dependencies.auth import get_current_user


router = APIRouter(
    tags=["Resumes"]
)


UPLOAD_DIR = "uploads/resumes"

os.makedirs(
    UPLOAD_DIR,
    exist_ok=True
)


ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx"
}


# --------------------------------
# UPLOAD RESUME
# --------------------------------

@router.post("/")
async def upload_resume(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user)
):

    extension = os.path.splitext(
        file.filename
    )[1].lower()

    if extension not in ALLOWED_EXTENSIONS:

        raise HTTPException(
            status_code=400,
            detail="Only PDF, DOC and DOCX files are allowed"
        )

    filename = (
        f"{uuid.uuid4()}"
        f"{extension}"
    )

    file_path = os.path.join(
        UPLOAD_DIR,
        filename
    )

    file_content = await file.read()

    with open(
        file_path,
        "wb"
    ) as buffer:

        buffer.write(file_content)

    resume_document = {

        "user_id": current_user["_id"],

        "file_url": f"/uploads/resumes/{filename}",

        "parsed_data": {},

        "ai_score": None,

        "suggestions": [],

        "is_default": False,

        "created_at": datetime.now(
            timezone.utc
        ),

        "updated_at": datetime.now(
            timezone.utc
        )
    }

    result = resumes_collection.insert_one(
        resume_document
    )

    return {
        "message": "Resume uploaded successfully",
        "resume_id": str(result.inserted_id),
        "file_url": resume_document["file_url"]
    }


# --------------------------------
# GET MY RESUMES
# --------------------------------

@router.get("/")
def get_my_resumes(
    current_user=Depends(get_current_user)
):

    resumes = list(
        resumes_collection.find({
            "user_id": current_user["_id"]
        })
    )

    for resume in resumes:

        resume["id"] = str(
            resume.pop("_id")
        )

        resume["user_id"] = str(
            resume["user_id"]
        )

    return resumes


# --------------------------------
# GET SINGLE RESUME
# --------------------------------

@router.get("/{resume_id}")
def get_resume(
    resume_id: str,
    current_user=Depends(get_current_user)
):

    if not ObjectId.is_valid(resume_id):

        raise HTTPException(
            status_code=400,
            detail="Invalid resume ID"
        )

    resume = resumes_collection.find_one({
        "_id": ObjectId(resume_id),
        "user_id": current_user["_id"]
    })

    if not resume:

        raise HTTPException(
            status_code=404,
            detail="Resume not found"
        )

    resume["id"] = str(
        resume.pop("_id")
    )

    resume["user_id"] = str(
        resume["user_id"]
    )

    return resume


# --------------------------------
# DELETE RESUME
# --------------------------------

@router.delete("/{resume_id}")
def delete_resume(
    resume_id: str,
    current_user=Depends(get_current_user)
):

    if not ObjectId.is_valid(resume_id):

        raise HTTPException(
            status_code=400,
            detail="Invalid resume ID"
        )

    resume = resumes_collection.find_one({
        "_id": ObjectId(resume_id),
        "user_id": current_user["_id"]
    })

    if not resume:

        raise HTTPException(
            status_code=404,
            detail="Resume not found"
        )

    resumes_collection.delete_one({
        "_id": ObjectId(resume_id)
    })

    if resume.get("file_url"):

        file_path = resume["file_url"].lstrip("/")

        if os.path.exists(file_path):

            os.remove(file_path)

    return {
        "message": "Resume deleted successfully"
    }


# --------------------------------
# SET DEFAULT RESUME
# --------------------------------

@router.patch("/{resume_id}/default")
def set_default_resume(
    resume_id: str,
    current_user=Depends(get_current_user)
):

    if not ObjectId.is_valid(resume_id):

        raise HTTPException(
            status_code=400,
            detail="Invalid resume ID"
        )

    resume = resumes_collection.find_one({
        "_id": ObjectId(resume_id),
        "user_id": current_user["_id"]
    })

    if not resume:

        raise HTTPException(
            status_code=404,
            detail="Resume not found"
        )

    # Remove old default
    resumes_collection.update_many(
        {
            "user_id": current_user["_id"]
        },
        {
            "$set": {
                "is_default": False
            }
        }
    )

    # Set new default
    resumes_collection.update_one(
        {
            "_id": ObjectId(resume_id)
        },
        {
            "$set": {
                "is_default": True,
                "updated_at": datetime.now(
                    timezone.utc
                )
            }
        }
    )

    # Update profile resume_id
    user_profiles_collection.update_one(
        {
            "user_id": current_user["_id"]
        },
        {
            "$set": {
                "resume_id": ObjectId(resume_id)
            }
        }
    )

    return {
        "message": "Default resume updated successfully"
    }