from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException
)

from app.database import (
    users_collection,
    user_profiles_collection
)

from app.dependencies.auth import get_current_user

from app.schemas.users import (
    UserUpdate,
    ProfileUpdate
)


router = APIRouter(
    tags=["Users"]
)


# --------------------------------
# GET CURRENT USER
# --------------------------------

@router.get("/me")
def get_current_user_profile(
    current_user=Depends(get_current_user)
):

    return {
        "id": str(current_user["_id"]),
        "name": current_user["name"],
        "email": current_user["email"],
        "phone": current_user.get("phone"),
        "role": current_user["role"],
        "auth_provider": current_user["auth_provider"],
        "profile_image": current_user.get("profile_image"),
        "is_active": current_user["is_active"],
        "created_at": current_user["created_at"]
    }


# --------------------------------
# UPDATE BASIC USER
# --------------------------------

@router.put("/me")
def update_current_user(
    data: UserUpdate,
    current_user=Depends(get_current_user)
):

    update_data = {
        key: value
        for key, value in data.model_dump().items()
        if value is not None
    }

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="No data provided"
        )

    update_data["updated_at"] = datetime.now(
        timezone.utc
    )

    users_collection.update_one(
        {
            "_id": current_user["_id"]
        },
        {
            "$set": update_data
        }
    )

    return {
        "message": "User updated successfully"
    }


# --------------------------------
# GET USER PROFILE
# --------------------------------

@router.get("/me/profile")
def get_my_profile(
    current_user=Depends(get_current_user)
):

    profile = user_profiles_collection.find_one({
        "user_id": current_user["_id"]
    })

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Profile not found"
        )

    profile["id"] = str(
        profile.pop("_id")
    )

    profile["user_id"] = str(
        profile["user_id"]
    )

    return profile


# --------------------------------
# UPDATE USER PROFILE
# --------------------------------

@router.put("/me/profile")
def update_my_profile(
    data: ProfileUpdate,
    current_user=Depends(get_current_user)
):

    update_data = {
        key: value
        for key, value in data.model_dump().items()
        if value is not None
    }

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="No data provided"
        )

    update_data["updated_at"] = datetime.now(
        timezone.utc
    )

    user_profiles_collection.update_one(
        {
            "user_id": current_user["_id"]
        },
        {
            "$set": update_data
        }
    )

    return {
        "message": "Profile updated successfully"
    }