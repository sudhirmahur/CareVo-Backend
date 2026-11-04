from app.utils import now


def create_user_document(name: str, email: str, password_hash: str, role: str = "user", phone=None) -> dict:
    ts = now()
    return {
        "name": name,
        "email": email,
        "password_hash": password_hash,
        "phone": phone,
        "auth_provider": "email",
        "provider_id": None,
        "role": role,  # admin | recruiter | user
        "is_active": True,
        "profile_image": None,
        "is_premium": False,
        "premium_until": None,
        "failed_logins": 0,
        "locked_until": None,
        "tokens_valid_after": None,
        "created_at": ts,
        "updated_at": ts,
    }


def create_profile_document(user_id) -> dict:
    ts = now()
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
        "created_at": ts,
        "updated_at": ts,
    }
