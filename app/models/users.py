from datetime import datetime, timezone


def create_user_document(
    name: str,
    email: str,
    password_hash: str
):
    now = datetime.now(timezone.utc)

    return {
        "name": name,
        "email": email,
        "password_hash": password_hash,

        "phone": None,

        "auth_provider": "email",

        "provider_id": None,

        "role": "user",

        "is_active": True,

        "profile_image": None,

        "created_at": now,

        "updated_at": now
    }


def create_profile_document(user_id):

    now = datetime.now(timezone.utc)

    return {
        "user_id": user_id,

        "bio": "",

        "skills": [],

        "experience_years": 0,

        "current_position": "",

        "location": "",

        "github_url": "",

        "linkedin_url": "",

        "portfolio_url": "",

        "resume_id": None,

        "created_at": now,

        "updated_at": now
    }