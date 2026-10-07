from datetime import datetime, timezone

from bson import ObjectId

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status
)

from app.database import (
    applications_collection,
    jobs_collection,
    resumes_collection
)

from app.dependencies.auth import get_current_user

from app.schemas.applications import ApplicationCreate


router = APIRouter(
    tags=["Applications"]
)


# --------------------------------
# APPLY FOR JOB
# --------------------------------

@router.post(
    "/jobs/{job_id}/apply",
    status_code=status.HTTP_201_CREATED
)
def apply_for_job(
    job_id: str,
    data: ApplicationCreate,
    current_user=Depends(get_current_user)
):

    if not ObjectId.is_valid(job_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid job ID"
        )

    if not ObjectId.is_valid(data.resume_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid resume ID"
        )

    job = jobs_collection.find_one({
        "_id": ObjectId(job_id),
        "is_active": True
    })

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    resume = resumes_collection.find_one({
        "_id": ObjectId(data.resume_id),
        "user_id": current_user["_id"]
    })

    if not resume:
        raise HTTPException(
            status_code=404,
            detail="Resume not found"
        )

    existing_application = applications_collection.find_one({
        "job_id": ObjectId(job_id),
        "user_id": current_user["_id"]
    })

    if existing_application:
        raise HTTPException(
            status_code=409,
            detail="You have already applied for this job"
        )

    application = {
        "job_id": ObjectId(job_id),
        "user_id": current_user["_id"],
        "resume_id": ObjectId(data.resume_id),
        "cover_letter": data.cover_letter,
        "status": "applied",
        "applied_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }

    result = applications_collection.insert_one(application)

    return {
        "message": "Application submitted successfully",
        "application_id": str(result.inserted_id)
    }


# --------------------------------
# GET MY APPLICATIONS
# --------------------------------

@router.get("")
def get_my_applications(
    current_user=Depends(get_current_user)
):

    applications = list(
        applications_collection.find({
            "user_id": current_user["_id"]
        }).sort(
            "applied_at",
            -1
        )
    )

    result = []

    for application in applications:

        result.append({
            "id": str(application["_id"]),
            "job_id": str(application["job_id"]),
            "resume_id": str(application["resume_id"]),
            "status": application["status"],
            "cover_letter": application.get("cover_letter"),
            "applied_at": application["applied_at"],
            "updated_at": application["updated_at"]
        })

    return result


# --------------------------------
# GET SINGLE APPLICATION
# --------------------------------

@router.get("/{application_id}")
def get_application(
    application_id: str,
    current_user=Depends(get_current_user)
):

    if not ObjectId.is_valid(application_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid application ID"
        )

    application = applications_collection.find_one({
        "_id": ObjectId(application_id),
        "user_id": current_user["_id"]
    })

    if not application:
        raise HTTPException(
            status_code=404,
            detail="Application not found"
        )

    return {
        "id": str(application["_id"]),
        "job_id": str(application["job_id"]),
        "resume_id": str(application["resume_id"]),
        "status": application["status"],
        "cover_letter": application.get("cover_letter"),
        "applied_at": application["applied_at"],
        "updated_at": application["updated_at"]
    }