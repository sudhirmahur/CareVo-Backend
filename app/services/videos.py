from typing import Optional

from app.database import (
    companies_collection,
    jobs_collection,
    users_collection,
    video_likes_collection,
    video_saves_collection,
)
from app.utils import ser


def shape_videos(videos: list, user: Optional[dict] = None) -> list:
    authors = {u["_id"]: u for u in users_collection.find({"_id": {"$in": [v["user_id"] for v in videos if v.get("user_id")]}})}
    companies = {c["_id"]: c for c in companies_collection.find({"_id": {"$in": [v["company_id"] for v in videos if v.get("company_id")]}})}
    jobs = {j["_id"]: j for j in jobs_collection.find({"_id": {"$in": [v["job_id"] for v in videos if v.get("job_id")]}})}
    liked, saved = set(), set()
    if user is not None and videos:
        ids = [v["_id"] for v in videos]
        liked = {x["video_id"] for x in video_likes_collection.find({"user_id": user["_id"], "video_id": {"$in": ids}})}
        saved = {x["video_id"] for x in video_saves_collection.find({"user_id": user["_id"], "video_id": {"$in": ids}})}
    out = []
    for v in videos:
        item = ser(v)
        a = authors.get(v.get("user_id"))
        c = companies.get(v.get("company_id"))
        item["author"] = {"id": str(a["_id"]), "name": a["name"], "role": a["role"], "profile_image": a.get("profile_image")} if a else None
        item["company_name"] = c["name"] if c else None
        item["company_logo"] = c.get("logo_url") if c else None
        j = jobs.get(v.get("job_id"))
        item["job"] = (
            {
                "id": str(j["_id"]), "title": j.get("title"), "location": j.get("location"), "job_type": j.get("job_type"),
                "apply_type": "external" if j.get("apply_url") else "internal",
                "is_open": j.get("status") == "approved" and j.get("is_active", True),
            }
            if j else None
        )
        if user is not None:
            item["is_liked"] = v["_id"] in liked
            item["is_saved"] = v["_id"] in saved
        out.append(item)
    return out
