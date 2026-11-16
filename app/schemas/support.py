from typing import Literal, Optional

from pydantic import BaseModel, Field

Category = Literal["account", "billing", "jobs", "technical", "recruiting", "report", "other"]
Priority = Literal["low", "normal", "high", "urgent"]
Status = Literal["open", "in_progress", "waiting", "resolved", "closed"]


class TicketIn(BaseModel):
    subject: str = Field(min_length=4, max_length=140)
    category: Category = "other"
    message: str = Field(min_length=5, max_length=4000)
    priority: Priority = "normal"


class ReplyIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AdminTicketUpdate(BaseModel):
    status: Optional[Status] = None
    priority: Optional[Priority] = None
    assigned_to: Optional[str] = None  # admin user id, "" to unassign


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class FeedbackIn(BaseModel):
    category: Literal["bug", "idea", "praise", "feature_request", "other"] = "other"
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    message: str = Field(min_length=5, max_length=3000)


class FeedbackUpdate(BaseModel):
    status: Optional[Literal["new", "reviewing", "planned", "done", "declined"]] = None
    response: Optional[str] = Field(default=None, max_length=2000)
