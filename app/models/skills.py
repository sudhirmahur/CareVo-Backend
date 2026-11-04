from app.utils import now


def create_user_skill_document(user_id, skill_id, proficiency) -> dict:
    return {"user_id": user_id, "skill_id": skill_id, "proficiency": proficiency, "created_at": now()}
