from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, StringConstraints, model_validator

S = lambda lo, hi: Annotated[str, StringConstraints(strip_whitespace=True, min_length=lo, max_length=hi)]  # noqa: E731


class PrepIn(BaseModel):
    category: S(2, 60)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    question: S(5, 400)
    answer: S(5, 4000)
    is_active: bool = True


class PrepUpdate(BaseModel):
    category: Optional[S(2, 60)] = None
    difficulty: Optional[Literal["easy", "medium", "hard"]] = None
    question: Optional[S(5, 400)] = None
    answer: Optional[S(5, 4000)] = None
    is_active: Optional[bool] = None


class PrepProgress(BaseModel):
    status: Literal["known", "review", "none"]


class QuestionIn(BaseModel):
    q: S(5, 400)
    options: list[S(1, 200)] = Field(min_length=2, max_length=6)
    answer: int = Field(ge=0, le=5)
    explanation: S(0, 500) = ""

    @model_validator(mode="after")
    def _answer_in_range(self):
        if self.answer >= len(self.options):
            raise ValueError("answer must point to one of the options")
        return self


class AssessmentIn(BaseModel):
    title: S(3, 100)
    skill: S(1, 60)
    description: S(0, 300) = ""
    pass_percent: int = Field(default=60, ge=1, le=100)
    is_active: bool = True
    questions: list[QuestionIn] = Field(min_length=1, max_length=50)


class AssessmentUpdate(BaseModel):
    title: Optional[S(3, 100)] = None
    skill: Optional[S(1, 60)] = None
    description: Optional[S(0, 300)] = None
    pass_percent: Optional[int] = Field(default=None, ge=1, le=100)
    is_active: Optional[bool] = None
    questions: Optional[list[QuestionIn]] = Field(default=None, min_length=1, max_length=50)


class Submission(BaseModel):
    answers: list[Optional[int]] = Field(max_length=50)


class ExperienceItem(BaseModel):
    role: S(0, 100) = ""
    company: S(0, 100) = ""
    start: S(0, 30) = ""
    end: S(0, 30) = ""
    details: S(0, 1500) = ""


class EducationItem(BaseModel):
    degree: S(0, 120) = ""
    school: S(0, 120) = ""
    start: S(0, 30) = ""
    end: S(0, 30) = ""


class ProjectItem(BaseModel):
    name: S(0, 100) = ""
    link: S(0, 300) = ""
    details: S(0, 800) = ""


class ResumeBuild(BaseModel):
    template: Literal["classic", "modern"] = "modern"
    full_name: S(0, 100) = ""
    headline: S(0, 120) = ""
    email: S(0, 254) = ""
    phone: S(0, 30) = ""
    location: S(0, 100) = ""
    links: list[S(1, 200)] = Field(default=[], max_length=6)
    summary: S(0, 1500) = ""
    skills: list[S(1, 50)] = Field(default=[], max_length=40)
    experience: list[ExperienceItem] = Field(default=[], max_length=10)
    education: list[EducationItem] = Field(default=[], max_length=8)
    projects: list[ProjectItem] = Field(default=[], max_length=8)
