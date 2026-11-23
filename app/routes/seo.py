"""Search / share helpers: robots.txt, sitemap.xml and a share page with Open Graph tags for each job.

The React app is a single-page app, so link previews (WhatsApp, LinkedIn, Facebook, X) cannot read job details from
it. Share links point at /api/public/share/jobs/{id} instead: a tiny HTML page with the right meta tags that sends
real visitors straight on to the job in the app.
"""
from html import escape
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from app import config
from app.database import companies_collection, jobs_collection
from app.utils import oid

router = APIRouter()
root_router = APIRouter()


def _public_job(job_id: str):
    try:
        job = jobs_collection.find_one({"_id": oid(job_id, "job ID")})
    except Exception:
        return None
    return job if job and job.get("status") == "approved" and job.get("is_active") else None


def _snippet(text: str, n: int = 200) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


@router.get("/share/jobs/{job_id}", response_class=HTMLResponse)
def share_job(job_id: str):
    job = _public_job(job_id)
    if not job:
        return HTMLResponse(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={escape(config.PUBLIC_APP_URL, quote=True)}/jobs"><title>CareVo</title>', status_code=404)
    company = companies_collection.find_one({"_id": job.get("company_id")}) if job.get("company_id") else None
    name = (company or {}).get("name") or job.get("company_name") or "CareVo"
    title = f"{job['title']} at {name}"
    desc = _snippet(job.get("description") or f"Apply for {job['title']} at {name} on CareVo.")
    url = f"{config.PUBLIC_APP_URL}/jobs/{job['_id']}"
    image = (company or {}).get("logo") or ""
    image_tag = f'<meta property="og:image" content="{escape(image, quote=True)}">' if image.startswith("https://") else ""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{escape(title)} | CareVo</title>
<meta name="description" content="{escape(desc, quote=True)}"><link rel="canonical" href="{escape(url, quote=True)}">
<meta property="og:type" content="website"><meta property="og:site_name" content="CareVo"><meta property="og:title" content="{escape(title, quote=True)}">
<meta property="og:description" content="{escape(desc, quote=True)}"><meta property="og:url" content="{escape(url, quote=True)}">{image_tag}
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="{escape(title, quote=True)}"><meta name="twitter:description" content="{escape(desc, quote=True)}">
<meta http-equiv="refresh" content="0;url={escape(url, quote=True)}"></head><body><p><a href="{escape(url, quote=True)}">Open {escape(title)} on CareVo</a></p></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "public, max-age=300"})


@router.get("/sitemap.xml")
def sitemap():
    urls = [f"{config.PUBLIC_APP_URL}/", f"{config.PUBLIC_APP_URL}/jobs"]
    for j in jobs_collection.find({"status": "approved", "is_active": True}, {"_id": 1, "updated_at": 1}).sort("updated_at", -1).limit(5000):
        urls.append(f"{config.PUBLIC_APP_URL}/jobs/{j['_id']}")
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(f"<url><loc>{xml_escape(u)}</loc></url>" for u in urls) + "\n</urlset>"
    return Response(body, media_type="application/xml", headers={"Cache-Control": "public, max-age=900"})


@root_router.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /api/\nDisallow: /uploads/\n"
