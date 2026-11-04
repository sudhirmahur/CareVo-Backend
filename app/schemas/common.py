from typing import Annotated, Optional

from pydantic import AfterValidator, StringConstraints

# E-mail without the optional `email-validator` dependency.
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        max_length=254,
        pattern=r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$",
    ),
]


def _http_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not (value.lower().startswith("http://") or value.lower().startswith("https://")):
        raise ValueError("URL must start with http:// or https://")
    if len(value) > 2048 or " " in value:
        raise ValueError("Invalid URL")
    return value


HttpUrl = Annotated[Optional[str], AfterValidator(_http_url)]


def _password(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(value) > 100:
        raise ValueError("Password is too long (max 100 characters)")
    if not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise ValueError("Password must contain at least one letter and one number")
    if value.lower() in {"password1", "password123", "12345678a", "qwerty123", "abc12345"}:
        raise ValueError("That password is too common")
    return value


Password = Annotated[str, AfterValidator(_password)]


def _media_url(value: Optional[str]) -> Optional[str]:
    """http(s) link or a file stored by this API (/uploads/...)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    low = value.lower()
    if len(value) > 2048 or any(c in value for c in " \r\n\t<>\"'`"):
        raise ValueError("Invalid URL")
    if low.startswith(("http://", "https://")) or (value.startswith("/uploads/") and ".." not in value):
        return value
    raise ValueError("URL must start with http:// or https://")


MediaUrl = Annotated[Optional[str], AfterValidator(_media_url)]
