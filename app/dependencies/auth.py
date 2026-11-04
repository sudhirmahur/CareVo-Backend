"""Auth dependencies: current user, optional user, role guards."""
import jwt
from bson import ObjectId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import JWT_ALGORITHM, JWT_SECRET
from app.database import users_collection

security = HTTPBearer(auto_error=False)


def _decode(token: str) -> tuple[ObjectId, int]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    sub = payload.get("sub")
    if not sub or not ObjectId.is_valid(sub):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return ObjectId(sub), int(payload.get("iat", 0))


def _load(decoded: tuple[ObjectId, int]) -> dict:
    user_id, iat = decoded
    user = users_collection.find_one({"_id": user_id})
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    valid_after = user.get("tokens_valid_after")
    if valid_after is not None and iat < int(valid_after.timestamp()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Please sign in again.")
    if not user.get("is_active", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is blocked or inactive")
    return user


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return _load(_decode(credentials.credentials))


def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Public endpoints that personalise the response when a valid token is sent."""
    if credentials is None:
        return None
    try:
        return _load(_decode(credentials.credentials))
    except HTTPException:
        return None


def require_roles(*roles: str):
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission for this action")
        return user

    return checker


require_admin = require_roles("admin")
require_recruiter = require_roles("recruiter", "admin")
require_user = require_roles("user")
