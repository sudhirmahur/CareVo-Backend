"""Authenticated media upload (images, audio, video) -> Cloudinary or local disk."""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.database import media_collection
from app.dependencies.auth import get_current_user
from app.services import storage
from app.utils import now

router = APIRouter()

# purpose -> (allowed kinds, roles allowed)
PURPOSES = {
    "avatar": ({"image"}, {"user", "recruiter", "admin"}),
    "chat": ({"image", "audio", "video"}, {"user", "recruiter", "admin"}),
    "logo": ({"image"}, {"recruiter", "admin"}),
    "video": ({"video"}, {"recruiter", "admin"}),
    "thumbnail": ({"image"}, {"recruiter", "admin"}),
    "banner": ({"image"}, {"admin"}),
    "landing": ({"image"}, {"admin"}),
}
UPLOADS_PER_HOUR = 60


@router.get("/config")
def media_config(user=Depends(get_current_user)):
    return {"provider": storage.provider(), "limits_mb": storage.LIMITS_MB, "purposes": sorted(PURPOSES)}


@router.post("/upload", status_code=201)
def upload(file: UploadFile = File(...), purpose: str = Form("avatar"), user=Depends(get_current_user)):
    rule = PURPOSES.get(purpose)
    if not rule:
        raise HTTPException(422, "Unknown upload purpose")
    kinds, roles = rule
    if user["role"] not in roles:
        raise HTTPException(403, "You do not have permission to upload this kind of file")
    recent = media_collection.count_documents({"owner_id": user["_id"], "created_at": {"$gte": now() - timedelta(hours=1)}})
    if recent >= UPLOADS_PER_HOUR:
        raise HTTPException(429, "Upload limit reached. Please try again later.")
    # read at most limit+1 bytes so a huge body cannot exhaust memory
    max_bytes = max(storage.LIMITS_MB.values()) * 1024 * 1024 + 1
    content = file.file.read(max_bytes)
    expected: Optional[str] = next(iter(kinds)) if len(kinds) == 1 else None
    kind, fmt = storage.validate(content, expected)
    if kind not in kinds:
        raise HTTPException(400, f"{kind.capitalize()} files are not allowed here")
    result = storage.store(content, kind, fmt, purpose, user["_id"])
    return {"url": result["url"], "kind": kind, "provider": result["provider"], "bytes": result["bytes"], "name": (file.filename or "")[:120]}
