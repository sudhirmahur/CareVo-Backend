"""Premium membership helpers."""
from datetime import timezone
from typing import Optional

from app.utils import now


def is_premium(user: Optional[dict]) -> bool:
    if not user:
        return False
    if user.get("role") in ("recruiter", "admin"):
        return True  # staff are never gated
    if not user.get("is_premium"):
        return False
    until = user.get("premium_until")
    if until is None:
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > now()
