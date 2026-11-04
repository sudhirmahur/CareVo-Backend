"""Admin: edit the landing/login page, newsletter list, audit log."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from app.database import audit_logs_collection, newsletter_collection
from app.dependencies.auth import require_admin
from app.schemas.content import SECTION_MODELS
from app.services.audit import audit
from app.services.site_content import DEFAULTS, get_content, live_stats, reset_sections, save_sections
from app.utils import get_or_404, oid, paginate, ser

router = APIRouter()


@router.get("/site-content")
def read(admin=Depends(require_admin)):
    return {"content": get_content(), "defaults": DEFAULTS, "live_stats": live_stats(), "sections": list(SECTION_MODELS)}


@router.put("/site-content")
def update(body: dict, admin=Depends(require_admin)):
    """Body: {"hero": {...}, "stats": {...}}  - each provided section replaces the stored one."""
    if not body:
        raise HTTPException(400, "No sections provided")
    clean = {}
    for name, value in body.items():
        model = SECTION_MODELS.get(name)
        if not model:
            raise HTTPException(422, f"Unknown section: {name}")
        try:
            clean[name] = model.model_validate(value).model_dump()
        except ValidationError as e:
            msg = "; ".join(f"{name}.{'.'.join(str(p) for p in err['loc'])}: {err['msg'].replace('Value error, ', '')}" for err in e.errors()[:5])
            raise HTTPException(422, msg)
    content = save_sections(clean, admin["_id"])
    audit(admin, "site_content.update", ",".join(clean))
    return {"content": content}


@router.post("/site-content/reset")
def reset(sections: list[str], admin=Depends(require_admin)):
    bad = [s for s in sections if s not in SECTION_MODELS]
    if bad:
        raise HTTPException(422, f"Unknown section: {bad[0]}")
    audit(admin, "site_content.reset", ",".join(sections))
    return {"content": reset_sections(sections, admin["_id"])}


@router.get("/newsletter")
def newsletter(page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100), admin=Depends(require_admin)):
    return paginate(newsletter_collection, {}, page, limit, [("created_at", -1)])


@router.delete("/newsletter/{sub_id}")
def newsletter_delete(sub_id: str, admin=Depends(require_admin)):
    row = get_or_404(newsletter_collection, oid(sub_id, "subscriber ID"), "Subscriber")
    newsletter_collection.delete_one({"_id": row["_id"]})
    return {"message": "Removed"}


@router.get("/audit-logs")
def logs(page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100), admin=Depends(require_admin)):
    return paginate(audit_logs_collection, {}, page, limit, [("created_at", -1)])
