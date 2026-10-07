from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# --------------------------------
# USER REGISTER
# --------------------------------

class UserRegister(BaseModel):

    name: str = Field(
        min_length=2,
        max_length=100
    )

    email: EmailStr

    password: str = Field(
        min_length=6,
        max_length=100
    )


# --------------------------------
# USER LOGIN
# --------------------------------

class UserLogin(BaseModel):

    email: EmailStr

    password: str


# --------------------------------
# USER UPDATE
# --------------------------------

class UserUpdate(BaseModel):

    name: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=100
    )

    phone: Optional[str] = None


# --------------------------------
# PROFILE UPDATE
# --------------------------------

class ProfileUpdate(BaseModel):

    bio: Optional[str] = None

    experience_years: Optional[int] = None

    current_position: Optional[str] = None

    location: Optional[str] = None

    github_url: Optional[str] = None

    linkedin_url: Optional[str] = None

    portfolio_url: Optional[str] = None