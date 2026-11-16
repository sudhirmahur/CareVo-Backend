"""Banners and popups: content + schedule + audience."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import HttpUrl, MediaUrl

Audience = Literal["all", "guest", "user", "recruiter"]


def _check_window(starts_at, ends_at):
    if starts_at and ends_at and ends_at <= starts_at:
        raise ValueError("End time must be after the start time")


class BannerIn(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    text: Optional[str] = Field(default=None, max_length=300)
    image_url: MediaUrl = None
    link_url: HttpUrl = None
    audience: Audience = "all"
    style: Literal["info", "success", "warning", "promo"] = "promo"
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    is_active: bool = True

    @model_validator(mode="after")
    def _w(self):
        _check_window(self.starts_at, self.ends_at)
        return self


class BannerUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=150)
    text: Optional[str] = Field(default=None, max_length=300)
    image_url: MediaUrl = None
    link_url: HttpUrl = None
    audience: Optional[Audience] = None
    style: Optional[Literal["info", "success", "warning", "promo"]] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def _w(self):
        _check_window(self.starts_at, self.ends_at)
        return self


class PopupIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    message: str = Field(min_length=2, max_length=600)
    image_url: MediaUrl = None
    button_label: Optional[str] = Field(default=None, max_length=40)
    button_link: HttpUrl = None
    audience: Audience = "all"
    frequency: Literal["once", "daily", "always"] = "once"
    delay_seconds: int = Field(default=2, ge=0, le=120)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    is_active: bool = True

    @model_validator(mode="after")
    def _w(self):
        _check_window(self.starts_at, self.ends_at)
        if self.button_link and not self.button_label:
            raise ValueError("Add a button label for the link")
        return self


class PopupUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=120)
    message: Optional[str] = Field(default=None, min_length=2, max_length=600)
    image_url: MediaUrl = None
    button_label: Optional[str] = Field(default=None, max_length=40)
    button_link: HttpUrl = None
    audience: Optional[Audience] = None
    frequency: Optional[Literal["once", "daily", "always"]] = None
    delay_seconds: Optional[int] = Field(default=None, ge=0, le=120)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def _w(self):
        _check_window(self.starts_at, self.ends_at)
        return self
