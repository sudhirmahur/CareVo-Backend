from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import HttpUrl, MediaUrl


class VideoIn(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    description: str = Field(default="", max_length=3000)
    video_url: str = Field(min_length=5, max_length=2048)
    thumbnail_url: MediaUrl = None
    job_id: Optional[str] = Field(default=None, max_length=40)
    type: Literal["job", "company", "tip", "announcement"] = "tip"
    tags: list[str] = []
    is_public: bool = True


class CommentIn(BaseModel):
    comment: str = Field(min_length=1, max_length=1000)


class Attachment(BaseModel):
    url: str = Field(max_length=2048)
    kind: Literal["image", "audio", "video"]
    name: str = Field(default="", max_length=120)

    @field_validator("url")
    @classmethod
    def _url_ok(cls, v: str) -> str:
        v = v.strip()
        low = v.lower()
        if any(c in v for c in " \r\n\t<>\"'`") or not (low.startswith("https://") or (v.startswith("/uploads/media/") and ".." not in v)):
            raise ValueError("Attachment must be a file uploaded through CareVo")
        return v


class MessageIn(BaseModel):
    receiver_id: str
    message: str = Field(default="", max_length=4000)
    attachment: Optional[Attachment] = None

    @model_validator(mode="after")
    def _something_to_send(self):
        if not self.message.strip() and not self.attachment:
            raise ValueError("Message cannot be empty")
        return self


class ReportIn(BaseModel):
    content_type: Literal["job", "user", "video", "comment"]
    content_id: str
    reason: str = Field(min_length=3, max_length=1000)


class ReportResolve(BaseModel):
    status: Literal["pending", "resolved"] = "resolved"
    action: Literal["none", "remove_content", "block_user"] = "none"
    note: Optional[str] = Field(default=None, max_length=500)


class FollowIn(BaseModel):
    following_id: str
    type: Literal["user", "company"] = "company"


class BroadcastIn(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    message: str = Field(min_length=2, max_length=1000)
    audience: Literal["all", "user", "recruiter"] = "all"


class BannerIn(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    image_url: MediaUrl = None
    link_url: HttpUrl = None
    is_active: bool = True


class SettingsIn(BaseModel):
    site_name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    support_email: Optional[str] = Field(default=None, max_length=120)
    allow_registration: Optional[bool] = None
    auto_approve_jobs: Optional[bool] = None
    auto_approve_companies: Optional[bool] = None
    maintenance_mode: Optional[bool] = None
    premium_gating: Optional[bool] = None


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    category_id: Optional[str] = None


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class AddUserSkill(BaseModel):
    skill_id: str
    proficiency: Literal["beginner", "intermediate", "advanced"] = "beginner"
