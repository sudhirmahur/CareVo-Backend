from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.common import Email, HttpUrl, MediaUrl, Password


class UserRegister(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: Email
    password: Password
    phone: Optional[str] = Field(default=None, max_length=20)
    # Admins can never be self-registered.
    role: Literal["user", "recruiter"] = "user"


class UserLogin(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=100)


class ForgotPassword(BaseModel):
    email: Email


class ResetPassword(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: Password


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=100)
    new_password: Password


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=20)
    profile_image: MediaUrl = None


class ProfileUpdate(BaseModel):
    bio: Optional[str] = Field(default=None, max_length=2000)
    experience_years: Optional[int] = Field(default=None, ge=0, le=60)
    current_position: Optional[str] = Field(default=None, max_length=120)
    location: Optional[str] = Field(default=None, max_length=120)
    github_url: HttpUrl = None
    linkedin_url: HttpUrl = None
    portfolio_url: HttpUrl = None
    skills: Optional[list[str]] = None


class AdminUserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: Email
    password: Password
    role: Literal["user", "recruiter", "admin"] = "user"


class AdminUserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    role: Optional[Literal["user", "recruiter", "admin"]] = None
    is_active: Optional[bool] = None


class GoogleLogin(BaseModel):
    access_token: str = Field(min_length=10, max_length=4096)
    role: Literal["user", "recruiter"] = "user"
