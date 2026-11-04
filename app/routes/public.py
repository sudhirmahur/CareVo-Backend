"""Public (no login) endpoints: landing-page content and newsletter sign-up."""
from fastapi import APIRouter
from pymongo.errors import DuplicateKeyError

from app.config import GOOGLE_CLIENT_ID
from app.database import newsletter_collection
from app.schemas.content import NewsletterIn
from app.services.site_content import get_content, live_stats
from app.utils import now

router = APIRouter()


@router.get("/site-content")
def site_content():
    return {
        "content": get_content(),
        "live_stats": live_stats(),
        "social_login": {"google_client_id": GOOGLE_CLIENT_ID or None},
    }


@router.post("/newsletter", status_code=201)
def subscribe(data: NewsletterIn):
    try:
        newsletter_collection.insert_one({"email": data.email, "created_at": now()})
    except DuplicateKeyError:
        pass  # same answer either way: do not reveal who is subscribed
    return {"message": "Thanks for subscribing!"}
