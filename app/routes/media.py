"""Authenticated media upload (images, audio, video) -> Cloudinary or local disk."""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app import config
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
    "popup": ({"image"}, {"admin"}),
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


# ------------------------------------------------------------------ direct browser -> Cloudinary video upload
# Large videos should not travel through this API (Render has memory and time limits). The browser asks for a signed
# upload ticket, sends the file straight to Cloudinary, then tells us the resulting URL. The ticket pins the folder
# (which contains the user's id), the allowed formats and a one-off public id, so a user cannot write elsewhere.
import secrets as _secrets
import time as _time

from pydantic import BaseModel, Field


class DirectCompleteIn(BaseModel):
    url: str = Field(max_length=600)


def _direct_prefix(user) -> str:
    return f"{config.CLOUDINARY_FOLDER}/video/{user['_id']}"


@router.post("/direct-upload/ticket")
def direct_upload_ticket(user=Depends(get_current_user)):
    if user["role"] not in ("recruiter", "admin"):
        raise HTTPException(403, "You do not have permission to upload this kind of file")
    if not storage.cloudinary_enabled():
        return {"direct": False, "max_mb": storage.LIMITS_MB["video"]}  # the client falls back to POST /media/upload
    recent = media_collection.count_documents({"owner_id": user["_id"], "created_at": {"$gte": now() - timedelta(hours=1)}})
    if recent >= UPLOADS_PER_HOUR:
        raise HTTPException(429, "Upload limit reached. Please try again later.")
    params = {"allowed_formats": "mp4,mov,webm", "public_id": f"{_direct_prefix(user)}/{_secrets.token_hex(10)}", "timestamp": str(int(_time.time()))}
    return {
        "direct": True,
        "upload_url": f"{config.CLOUDINARY_API_BASE}/{config.CLOUDINARY_CLOUD_NAME}/video/upload",
        "fields": {**params, "api_key": config.CLOUDINARY_API_KEY, "signature": storage._sign(params)},
        "max_mb": storage.LIMITS_MB["video"],
    }


@router.post("/direct-upload/complete", status_code=201)
def direct_upload_complete(data: DirectCompleteIn, user=Depends(get_current_user)):
    if user["role"] not in ("recruiter", "admin") or not storage.cloudinary_enabled():
        raise HTTPException(403, "Direct upload is not available")
    base = f"https://res.cloudinary.com/{config.CLOUDINARY_CLOUD_NAME}/video/upload/"
    url = data.url.strip()
    # only a file inside THIS user's folder of OUR Cloudinary account is accepted
    if not url.startswith(base) or f"/{_direct_prefix(user)}/" not in url or ".." in url or "?" in url or "#" in url:
        raise HTTPException(422, "That file was not uploaded with your upload ticket")
    if media_collection.find_one({"url": url}):
        raise HTTPException(409, "That upload was already registered")
    media_collection.insert_one({"url": url, "kind": "video", "provider": "cloudinary", "purpose": "video", "owner_id": user["_id"], "bytes": None, "created_at": now()})
    return {"url": url, "kind": "video", "provider": "cloudinary"}
