"""Video reels: feed, likes, comments, saves. Recruiters/admins publish; everyone engages."""
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.config import UPLOAD_DIR
from app.database import (
    companies_collection,
    jobs_collection,
    video_comments_collection,
    video_likes_collection,
    video_saves_collection,
    videos_collection,
    users_collection,
)
from app.dependencies.auth import get_current_user, get_optional_user, require_recruiter
from app.schemas.social import CommentIn, VideoIn
from app.schemas.jobs import ApplicationCreate
from app.services import storage
from app.services.notify import notify
from app.services.premium import is_premium
from app.services.settings import get_settings
from app.services.videos import shape_videos
from app.utils import clean_list, get_or_404, now, oid, paginate, rx, ser

router = APIRouter()

VIDEO_DIR = UPLOAD_DIR / "videos"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_EXT = {".mp4": b"\x00\x00\x00", ".webm": b"\x1a\x45\xdf\xa3"}


def _recount(video_id) -> None:
    videos_collection.update_one(
        {"_id": video_id},
        {
            "$set": {
                "likes_count": video_likes_collection.count_documents({"video_id": video_id}),
                "comments_count": video_comments_collection.count_documents({"video_id": video_id}),
            }
        },
    )


def _visible(video: dict, user: Optional[dict]) -> bool:
    if video.get("is_public", True) and not video.get("is_removed"):
        return True
    return bool(user and (user["role"] == "admin" or video.get("user_id") == user["_id"]))


@router.get("")
def feed(
    type: Optional[str] = Query(default=None, pattern="^(job|company|tip|announcement)$"),
    search: Optional[str] = None,
    has_job: Optional[bool] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=9, ge=1, le=30),
    user=Depends(get_optional_user),
):
    query: dict = {"is_public": True, "is_removed": {"$ne": True}}
    if type:
        query["type"] = type
    if has_job:
        query["job_id"] = {"$ne": None}
    if search:
        query["$or"] = [{"title": rx(search)}, {"description": rx(search)}, {"tags": rx(search)}]
    result = paginate(videos_collection, query, page, limit, [("created_at", -1)], mapper=lambda d: d)
    result["items"] = shape_videos(result["items"], user)
    return result


@router.get("/mine")
def mine(user=Depends(require_recruiter)):
    rows = list(videos_collection.find({"user_id": user["_id"]}).sort("created_at", -1))
    return {"items": shape_videos(rows, user), "total": len(rows)}


@router.get("/saved")
def saved(user=Depends(get_current_user)):
    rows = list(video_saves_collection.find({"user_id": user["_id"]}).sort("created_at", -1))
    vids = {v["_id"]: v for v in videos_collection.find({"_id": {"$in": [r["video_id"] for r in rows]}, "is_removed": {"$ne": True}})}
    ordered = [vids[r["video_id"]] for r in rows if r["video_id"] in vids]
    return {"items": shape_videos(ordered, user), "total": len(ordered)}


@router.post("/upload", status_code=201)
def upload_video(file: UploadFile = File(...), user=Depends(require_recruiter)):
    """Kept for older clients - same as POST /media/upload with purpose=video."""
    content = file.file.read(storage.LIMITS_MB["video"] * 1024 * 1024 + 1)
    kind, fmt = storage.validate(content, "video")
    return {"video_url": storage.store(content, kind, fmt, "video", user["_id"])["url"]}


def _check_job(data: VideoIn, user: dict):
    if not data.job_id:
        return None
    job = jobs_collection.find_one({"_id": oid(data.job_id, "job ID")})
    if not job or (user["role"] != "admin" and job.get("recruiter_id") != user["_id"]):
        raise HTTPException(422, "You can only link a video to one of your own jobs")
    return job["_id"]


@router.post("", status_code=201)
def create_video(data: VideoIn, user=Depends(require_recruiter)):
    if not (data.video_url.startswith(("http://", "https://", "/uploads/"))):
        raise HTTPException(422, "video_url must be an http(s) link or an uploaded file")
    company = companies_collection.find_one({"recruiter_id": user["_id"]})
    job_oid = _check_job(data, user)
    ts = now()
    doc = {
        **data.model_dump(),
        "job_id": job_oid,
        "tags": clean_list(data.tags),
        "user_id": user["_id"],
        "company_id": company["_id"] if company else None,
        "views_count": 0,
        "likes_count": 0,
        "comments_count": 0,
        "is_removed": False,
        "created_at": ts,
        "updated_at": ts,
    }
    doc["_id"] = videos_collection.insert_one(dict(doc)).inserted_id
    return shape_videos([doc], user)[0]


@router.get("/{video_id}")
def get_video(video_id: str, user=Depends(get_optional_user)):
    v = get_or_404(videos_collection, oid(video_id, "video ID"), "Video")
    if not _visible(v, user):
        raise HTTPException(404, "Video not found")
    videos_collection.update_one({"_id": v["_id"]}, {"$inc": {"views_count": 1}})
    v["views_count"] = v.get("views_count", 0) + 1
    return shape_videos([v], user)[0]


@router.put("/{video_id}")
def update_video(video_id: str, data: VideoIn, user=Depends(get_current_user)):
    v = get_or_404(videos_collection, oid(video_id, "video ID"), "Video")
    if user["role"] != "admin" and v.get("user_id") != user["_id"]:
        raise HTTPException(403, "You can only edit your own videos")
    values = data.model_dump()
    values["job_id"] = _check_job(data, user)
    values["tags"] = clean_list(values["tags"])
    values["updated_at"] = now()
    videos_collection.update_one({"_id": v["_id"]}, {"$set": values})
    return shape_videos([videos_collection.find_one({"_id": v["_id"]})], user)[0]


@router.delete("/{video_id}")
def delete_video(video_id: str, user=Depends(get_current_user)):
    v = get_or_404(videos_collection, oid(video_id, "video ID"), "Video")
    if user["role"] != "admin" and v.get("user_id") != user["_id"]:
        raise HTTPException(403, "You can only delete your own videos")
    delete_video_cascade(v)
    return {"message": "Video deleted"}


def delete_video_cascade(v: dict) -> None:
    vid = v["_id"]
    video_likes_collection.delete_many({"video_id": vid})
    video_saves_collection.delete_many({"video_id": vid})
    video_comments_collection.delete_many({"video_id": vid})
    videos_collection.delete_one({"_id": vid})
    url = v.get("video_url", "")
    if url.startswith("/uploads/videos/"):  # files from before the media service
        path = VIDEO_DIR / os.path.basename(url)
        if path.is_file():
            path.unlink()
    storage.delete_by_url(url)
    storage.delete_by_url(v.get("thumbnail_url"))


def _open_video(video_id: str, user) -> dict:
    v = get_or_404(videos_collection, oid(video_id, "video ID"), "Video")
    if not _visible(v, user):
        raise HTTPException(404, "Video not found")
    return v


@router.post("/{video_id}/like", status_code=201)
def like(video_id: str, user=Depends(get_current_user)):
    v = _open_video(video_id, user)
    res = video_likes_collection.update_one(
        {"video_id": v["_id"], "user_id": user["_id"]},
        {"$setOnInsert": {"video_id": v["_id"], "user_id": user["_id"], "created_at": now()}},
        upsert=True,
    )
    _recount(v["_id"])
    if res.upserted_id and v.get("user_id") and v["user_id"] != user["_id"]:
        notify(v["user_id"], "Someone liked your video", f"{user['name']} liked '{v['title']}'.", "system")
    return {"is_liked": True, "likes_count": video_likes_collection.count_documents({"video_id": v["_id"]})}


@router.delete("/{video_id}/like")
def unlike(video_id: str, user=Depends(get_current_user)):
    v = _open_video(video_id, user)
    video_likes_collection.delete_one({"video_id": v["_id"], "user_id": user["_id"]})
    _recount(v["_id"])
    return {"is_liked": False, "likes_count": video_likes_collection.count_documents({"video_id": v["_id"]})}


@router.post("/{video_id}/save", status_code=201)
def save(video_id: str, user=Depends(get_current_user)):
    v = _open_video(video_id, user)
    video_saves_collection.update_one(
        {"video_id": v["_id"], "user_id": user["_id"]},
        {"$setOnInsert": {"video_id": v["_id"], "user_id": user["_id"], "created_at": now()}},
        upsert=True,
    )
    return {"is_saved": True}


@router.delete("/{video_id}/save")
def unsave(video_id: str, user=Depends(get_current_user)):
    v = _open_video(video_id, user)
    video_saves_collection.delete_one({"video_id": v["_id"], "user_id": user["_id"]})
    return {"is_saved": False}


@router.get("/{video_id}/comments")
def comments(video_id: str, user=Depends(get_optional_user)):
    v = _open_video(video_id, user)
    rows = list(video_comments_collection.find({"video_id": v["_id"]}).sort("created_at", -1).limit(200))
    people = {u["_id"]: u for u in users_collection.find({"_id": {"$in": [r["user_id"] for r in rows]}})}
    items = []
    for r in rows:
        item = ser(r)
        p = people.get(r["user_id"])
        item["author"] = {"id": str(p["_id"]), "name": p["name"], "role": p["role"], "profile_image": p.get("profile_image")} if p else {"name": "Deleted user"}
        item["can_delete"] = bool(user and (user["role"] == "admin" or r["user_id"] == user["_id"] or v.get("user_id") == user["_id"]))
        items.append(item)
    return {"items": items, "total": len(items)}


@router.post("/{video_id}/comments", status_code=201)
def add_comment(video_id: str, data: CommentIn, user=Depends(get_current_user)):
    v = _open_video(video_id, user)
    text = data.comment.strip()
    if not text:
        raise HTTPException(422, "Comment cannot be empty")
    doc = {"video_id": v["_id"], "user_id": user["_id"], "comment": text, "created_at": now()}
    doc["_id"] = video_comments_collection.insert_one(dict(doc)).inserted_id
    _recount(v["_id"])
    if v.get("user_id") and v["user_id"] != user["_id"]:
        notify(v["user_id"], "New comment", f"{user['name']} commented on '{v['title']}'.", "system")
    item = ser(doc)
    item["author"] = {"id": str(user["_id"]), "name": user["name"], "role": user["role"], "profile_image": user.get("profile_image")}
    item["can_delete"] = True
    return item


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: str, user=Depends(get_current_user)):
    c = get_or_404(video_comments_collection, oid(comment_id, "comment ID"), "Comment")
    v = videos_collection.find_one({"_id": c["video_id"]}) or {}
    if not (user["role"] == "admin" or c["user_id"] == user["_id"] or v.get("user_id") == user["_id"]):
        raise HTTPException(403, "You cannot delete this comment")
    video_comments_collection.delete_one({"_id": c["_id"]})
    _recount(c["video_id"])
    return {"message": "Comment deleted"}


@router.post("/{video_id}/apply", status_code=201)
def apply_from_reel(video_id: str, user=Depends(get_current_user)):
    """Apply to the job linked to a reel. Candidates need Premium while `premium_gating` is on."""
    from app.routes.jobs import apply_for_job

    v = _open_video(video_id, user)
    if user["role"] != "user":
        raise HTTPException(403, "Only candidate accounts can apply")
    if not v.get("job_id"):
        raise HTTPException(400, "This reel is not linked to a job")
    if get_settings()["premium_gating"] and not is_premium(user):
        raise HTTPException(402, "Premium membership required to apply from Job Reels")
    return apply_for_job(str(v["job_id"]), ApplicationCreate(), user)
