"""Validation for the admin-editable landing page / login page content (strict: it is shown to the public)."""
import re
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

_ICON = re.compile(r"^[A-Za-z]{2,24}$")


def _link(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    low = v.lower()
    if len(v) > 2048 or any(c in v for c in "\r\n\t <>\"'`"):
        raise ValueError("Invalid link")
    if low.startswith(("http://", "https://", "mailto:", "tel:")):
        return v
    if (v.startswith("/") and not v.startswith("//")) or v.startswith("#"):
        return v
    raise ValueError("Links must start with http://, https://, /, # or mailto:")


def _img(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    low = v.lower()
    if len(v) > 2048 or any(c in v for c in "\r\n\t <>\"'`"):
        raise ValueError("Invalid image URL")
    if low.startswith(("http://", "https://")) or (v.startswith("/") and not v.startswith("//")):
        if low.endswith(".svg"):
            raise ValueError("SVG images are not allowed")
        return v
    raise ValueError("Image must be an uploaded file or an http(s) URL")


def _icon(v: str) -> str:
    if not _ICON.match(v or ""):
        raise ValueError("Invalid icon name")
    return v


Link = Annotated[str, AfterValidator(_link)]
Img = Annotated[str, AfterValidator(_img)]
Icon = Annotated[str, AfterValidator(_icon)]
T = lambda n: Annotated[str, StringConstraints(strip_whitespace=True, max_length=n)]  # noqa: E731


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NavLink(_M):
    label: T(40)
    href: Link


class Brand(_M):
    name: T(40) = Field(min_length=1)
    tagline: T(120) = ""
    logo_url: Img = ""


class Nav(_M):
    links: list[NavLink] = Field(max_length=8)
    login_label: T(30)
    signup_label: T(30)
    employer_label: T(40)
    employer_href: Link


class Hero(_M):
    badge: T(60)
    title: T(120)
    highlight: T(40) = ""
    subtitle: T(300)
    search_job_placeholder: T(60)
    search_location_placeholder: T(60)
    search_button: T(30)
    image_url: Img = ""


class StatItem(_M):
    icon: Icon
    label: T(40)
    value: T(20) = ""
    source: Literal["manual", "jobs", "companies", "seekers", "recruiters", "applications"] = "manual"


class Stats(_M):
    items: list[StatItem] = Field(max_length=6)


class CategoryItem(_M):
    name: T(40)
    count_label: T(30) = ""
    icon: Icon = "briefcase"
    color: Literal["blue", "red", "purple", "green", "orange", "indigo", "pink", "teal"] = "blue"
    query: T(80) = ""


class Categories(_M):
    title: T(80)
    view_all_label: T(30)
    items: list[CategoryItem] = Field(max_length=12)


class TrustedItem(_M):
    name: T(60)
    logo_url: Img = ""


class Trusted(_M):
    title: T(80)
    subtitle: T(160) = ""
    highlight: T(40) = ""
    items: list[TrustedItem] = Field(max_length=14)


class StepItem(_M):
    icon: Icon
    title: T(50)
    text: T(160)


class How(_M):
    title: T(80)
    highlight: T(40) = ""
    subtitle: T(240)
    cta_label: T(40)
    items: list[StepItem] = Field(max_length=6)


class Promo(_M):
    text: T(80)
    image_url: Img = ""


class TestimonialItem(_M):
    name: T(60)
    role: T(80)
    rating: int = Field(default=5, ge=1, le=5)
    quote: T(400)
    avatar_url: Img = ""


class Testimonials(_M):
    title: T(80)
    highlight: T(40) = ""
    subtitle: T(200)
    items: list[TestimonialItem] = Field(max_length=9)


class ArticleItem(_M):
    title: T(120)
    tag: T(40) = ""
    date: T(30) = ""
    image_url: Img = ""
    href: Link = ""


class Resources(_M):
    title: T(80)
    highlight: T(40) = ""
    subtitle: T(200)
    cta_label: T(40)
    cta_href: Link = ""
    items: list[ArticleItem] = Field(max_length=9)


class Cta(_M):
    title: T(120)
    subtitle: T(200)
    button_label: T(40)
    button_href: Link


class FooterColumn(_M):
    title: T(40)
    links: list[NavLink] = Field(max_length=10)


class Social(_M):
    kind: Literal["linkedin", "twitter", "instagram", "youtube", "facebook"]
    url: Link


class Footer(_M):
    tagline: T(200)
    columns: list[FooterColumn] = Field(max_length=5)
    socials: list[Social] = Field(max_length=6)
    newsletter_title: T(60)
    newsletter_text: T(160)
    newsletter_button: T(30)
    copyright: T(120)
    legal_links: list[NavLink] = Field(max_length=5)


class FeatureItem(_M):
    icon: Icon
    title: T(60)
    text: T(120)


class AuthPage(_M):
    badge: T(60)
    title: T(100)
    highlight: T(40) = ""
    subtitle: T(240)
    features: list[FeatureItem] = Field(max_length=5)
    quote: T(160)
    quote_author: T(40) = ""
    mobile_quote: T(120) = ""


class Seo(_M):
    title: T(70)
    description: T(200)


class Legal(_M):
    terms: T(20000)
    privacy: T(20000)


SECTION_MODELS = {
    "brand": Brand, "nav": Nav, "hero": Hero, "stats": Stats, "categories": Categories, "trusted": Trusted,
    "how": How, "promo": Promo, "testimonials": Testimonials, "resources": Resources, "cta": Cta,
    "footer": Footer, "auth_page": AuthPage, "seo": Seo, "legal": Legal,
}


class NewsletterIn(BaseModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=254, pattern=r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")]
