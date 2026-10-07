from fastapi import APIRouter, HTTPException, status

from app.database import (
    users_collection,
    user_profiles_collection,
)

from app.models.users import (
    create_user_document,
    create_profile_document,
)

from app.schemas.users import (
    UserRegister,
    UserLogin,
)

from app.services.auth import (
    hash_password,
    verify_password,
    create_access_token,
)


# =====================================================
# AUTH ROUTER
# =====================================================

router = APIRouter()


# =====================================================
# REGISTER
# POST /api/auth/register
# =====================================================

@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED
)
def register(user: UserRegister):

    try:
        # -----------------------------------------
        # Email
        # -----------------------------------------

        email = user.email.lower().strip()

        # -----------------------------------------
        # Check existing user
        # -----------------------------------------

        existing_user = users_collection.find_one({
            "email": email
        })

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered"
            )

        # -----------------------------------------
        # Hash password
        # -----------------------------------------

        password_hash = hash_password(
            user.password
        )

        # -----------------------------------------
        # Create user document
        # -----------------------------------------

        user_document = create_user_document(
            name=user.name.strip(),
            email=email,
            password_hash=password_hash
        )

        # -----------------------------------------
        # Insert user
        # -----------------------------------------

        result = users_collection.insert_one(
            user_document
        )

        user_id = result.inserted_id

        # -----------------------------------------
        # Create profile
        # -----------------------------------------

        profile_document = create_profile_document(
            user_id
        )

        user_profiles_collection.insert_one(
            profile_document
        )

        # -----------------------------------------
        # Success
        # -----------------------------------------

        return {
            "message": "Registration successful",
            "user_id": str(user_id)
        }

    except HTTPException:
        raise

    except Exception as e:
        print("REGISTER ERROR:", repr(e))

        raise HTTPException(
            status_code=500,
            detail=f"Registration failed: {str(e)}"
        )

# =====================================================
# LOGIN
# POST /api/auth/login
# =====================================================

@router.post("/login")
def login(user: UserLogin):

    # ---------------------------------------------
    # Find user
    # ---------------------------------------------

    email = user.email.lower().strip()

    existing_user = users_collection.find_one({
        "email": email
    })

    if not existing_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # ---------------------------------------------
    # Verify password
    # ---------------------------------------------

    password_valid = verify_password(
        user.password,
        existing_user["password_hash"],
    )

    if not password_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # ---------------------------------------------
    # Check account status
    # ---------------------------------------------

    if not existing_user.get(
        "is_active",
        False
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    # ---------------------------------------------
    # Create JWT token
    # ---------------------------------------------

    access_token = create_access_token(
        user_id=str(existing_user["_id"]),
        role=existing_user["role"],
    )

    # ---------------------------------------------
    # Response
    # ---------------------------------------------

    return {
        "message": "Login successful",

        "access_token": access_token,

        "token_type": "bearer",

        "user": {
            "id": str(existing_user["_id"]),
            "name": existing_user["name"],
            "email": existing_user["email"],
            "role": existing_user["role"],
        },
    }