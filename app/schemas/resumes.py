from typing import Optional

from pydantic import BaseModel


class ResumeResponse(BaseModel):

    id: str

    user_id: str

    file_url: str

    parsed_data: dict

    ai_score: Optional[float] = None

    suggestions: list[str] = []

    is_default: bool