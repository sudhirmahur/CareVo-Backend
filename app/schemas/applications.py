from typing import Optional

from pydantic import BaseModel


class ApplicationCreate(BaseModel):

    resume_id: str

    cover_letter: Optional[str] = None