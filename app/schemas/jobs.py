from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import HttpUrl

JobType = Literal["fulltime", "parttime", "internship", "contract", "freelance"]
WorkMode = Literal["remote", "hybrid", "onsite"]
JobStatus = Literal["pending", "approved", "rejected"]
ApplicationStatus = Literal["applied", "shortlisted", "rejected", "accepted"]


class CompanyIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    description: str = Field(default="", max_length=5000)
    logo_url: HttpUrl = None
    website: HttpUrl = None
    industry: str = Field(default="", max_length=100)
    size: Literal["startup", "sme", "enterprise"] = "startup"
    location: str = Field(default="", max_length=150)


class CompanyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    description: Optional[str] = Field(default=None, max_length=5000)
    logo_url: HttpUrl = None
    website: HttpUrl = None
    industry: Optional[str] = Field(default=None, max_length=100)
    size: Optional[Literal["startup", "sme", "enterprise"]] = None
    location: Optional[str] = Field(default=None, max_length=150)


MAX_QUESTIONS = 25


class ScreeningQuestion(BaseModel):
    id: Optional[str] = Field(default=None, max_length=40)
    # text = short text, long_text, choice = single choice, multiple = multiple choice, yesno, number, email, file
    type: Literal["text", "long_text", "choice", "multiple", "yesno", "number", "email", "file"] = "text"
    question: str = Field(min_length=3, max_length=300)
    options: list[str] = Field(default_factory=list, max_length=12)
    required: bool = True
    kind: Literal["general", "experience", "qualification"] = "general"

    @model_validator(mode="after")
    def _opts(self):
        self.options = [o.strip() for o in self.options if o.strip()]
        if self.type in ("choice", "multiple") and len(self.options) < 2:
            raise ValueError("Choice questions need at least two options")
        if len({o.lower() for o in self.options}) != len(self.options):
            raise ValueError("Options must be different from each other")
        if any(len(o) > 100 for o in self.options):
            raise ValueError("Each option can be at most 100 characters")
        if self.type not in ("choice", "multiple"):
            self.options = []
        return self


class JobIn(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=10, max_length=10000)
    company_id: Optional[str] = None  # required for admins, ignored for recruiters
    requirements: list[str] = []
    responsibilities: list[str] = []
    skills: list[str] = []
    job_type: JobType = "fulltime"
    salary_min: Optional[int] = Field(default=None, ge=0)
    salary_max: Optional[int] = Field(default=None, ge=0)
    location: str = Field(default="", max_length=150)
    # The employer's own job page. When set, "Apply" records the application in
    # Carevo history and then sends the candidate to this link.
    apply_url: HttpUrl = None
    application_deadline: Optional[datetime] = None
    is_active: bool = True
    work_mode: Optional[WorkMode] = None
    screening_questions: list["ScreeningQuestion"] = Field(default_factory=list, max_length=MAX_QUESTIONS)

    @model_validator(mode="after")
    def _salary_range(self):
        if self.salary_min is not None and self.salary_max is not None and self.salary_max < self.salary_min:
            raise ValueError("salary_max must be greater than or equal to salary_min")
        return self


class JobUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=150)
    description: Optional[str] = Field(default=None, min_length=10, max_length=10000)
    requirements: Optional[list[str]] = None
    responsibilities: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    job_type: Optional[JobType] = None
    salary_min: Optional[int] = Field(default=None, ge=0)
    salary_max: Optional[int] = Field(default=None, ge=0)
    location: Optional[str] = Field(default=None, max_length=150)
    apply_url: HttpUrl = None
    clear_apply_url: bool = False
    application_deadline: Optional[datetime] = None
    is_active: Optional[bool] = None
    work_mode: Optional[WorkMode] = None
    screening_questions: Optional[list[ScreeningQuestion]] = Field(default=None, max_length=MAX_QUESTIONS)


class ApplicationCreate(BaseModel):
    resume_id: Optional[str] = None
    cover_letter: Optional[str] = Field(default=None, max_length=5000)
    # question id -> text, a list of picked options (multiple choice) or an uploaded file id (file questions)
    answers: dict[str, Union[str, list[str]]] = Field(default_factory=dict)


class ApplicationStatusUpdate(BaseModel):
    status: ApplicationStatus
    note: Optional[str] = Field(default=None, max_length=1000)


class InterviewIn(BaseModel):
    application_id: str
    scheduled_at: datetime
    mode: Literal["online", "offline"] = "online"
    meeting_link: HttpUrl = None
    location: Optional[str] = Field(default=None, max_length=200)


class InterviewUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    mode: Optional[Literal["online", "offline"]] = None
    meeting_link: HttpUrl = None
    location: Optional[str] = Field(default=None, max_length=200)
    status: Optional[Literal["scheduled", "completed", "cancelled"]] = None
    feedback: Optional[str] = Field(default=None, max_length=3000)


class AdminJobStatus(BaseModel):
    status: JobStatus
    reason: Optional[str] = Field(default=None, max_length=500)
