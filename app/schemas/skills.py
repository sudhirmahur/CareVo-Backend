from pydantic import BaseModel


class AddUserSkill(BaseModel):

    skill_id: str

    proficiency: str = "beginner"