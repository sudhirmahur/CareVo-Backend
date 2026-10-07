from datetime import datetime, timezone


def create_user_skill_document(
    user_id,
    skill_id,
    proficiency
):

    return {
        "user_id": user_id,
        "skill_id": skill_id,
        "proficiency": proficiency,
        "created_at": datetime.now(timezone.utc)
    }