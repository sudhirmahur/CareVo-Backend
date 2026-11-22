"""Offer letters: rendering (text + PDF paragraphs) and API shape."""
from datetime import timezone

from app.utils import ser

DEFAULT_TERMS = (
    "This offer is subject to satisfactory background verification and submission of the required documents. "
    "Your employment will be governed by the company's policies. Either party may end the employment by giving the notice period stated by the company."
)
DEFAULT_BODY = (
    "Dear {{candidate_name}},\n\n"
    "We are delighted to offer you the position of {{designation}} at {{company}}.\n\n"
    "Annual compensation: {{salary}}\nExpected joining date: {{joining_date}}\n\n"
    "Terms and conditions:\n{{terms}}\n\n"
    "Please respond to this offer on CareVo by {{expires}}.\n\n"
    "Warm regards,\n{{recruiter_name}}\n{{company}}"
)


# Visual styles of the offer letter (PDF + on-screen preview share these keys and colours).
STYLES = {
    "modern_corporate": {"name": "Modern Corporate", "description": "Bold colour band, clean sans-serif, structured summary table.", "primary": "#1d4ed8", "accent": "#38bdf8", "tint": "#eff6ff", "head": "band", "serif": False},
    "minimal_professional": {"name": "Minimal Professional", "description": "Quiet and spacious: thin rules, black text, lots of white space.", "primary": "#111827", "accent": "#9ca3af", "tint": "#f3f4f6", "head": "minimal", "serif": False},
    "premium_corporate": {"name": "Premium Corporate", "description": "Deep navy letterhead with gold detailing and serif type.", "primary": "#0f172a", "accent": "#b8923a", "tint": "#faf6ec", "head": "premium", "serif": True},
    "classic_formal": {"name": "Classic Formal", "description": "Traditional centred letterhead with double rule and serif type.", "primary": "#1f2937", "accent": "#1f2937", "tint": "#f5f5f4", "head": "classic", "serif": True},
    "modern_gradient": {"name": "Modern Gradient", "description": "Vibrant gradient header with a friendly, contemporary feel.", "primary": "#6366f1", "accent": "#ec4899", "tint": "#f5f3ff", "head": "gradient", "serif": False},
}
DEFAULT_STYLE = "modern_corporate"


def style_of(o: dict) -> dict:
    return STYLES.get(o.get("template_style") or DEFAULT_STYLE, STYLES[DEFAULT_STYLE])


def styles_out() -> list:
    return [{"key": k, **{x: v[x] for x in ("name", "description", "primary", "accent", "tint", "head", "serif")}} for k, v in STYLES.items()]


def money(amount, currency="INR") -> str:
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        return str(amount)
    s = f"{n:,}" if currency != "INR" else _indian(n)
    return f"{sym}{s}"


def _indian(n: int) -> str:
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + s


def context(o: dict) -> dict:
    j = o.get("joining_date")
    e = o.get("expires_at")
    fmt = lambda d: d.strftime("%d %B %Y") if d else "-"
    return {
        "candidate_name": o["candidate_name"], "designation": o["designation"], "company": o["company_name"], "salary": money(o["salary"], o.get("currency", "INR")),
        "joining_date": fmt(j), "terms": o.get("terms") or DEFAULT_TERMS, "recruiter_name": o.get("recruiter_name", ""), "expires": fmt(e) if e else "the date agreed with us",
        "date": fmt(o.get("sent_at") or o.get("created_at")),
        "probation": ("None" if not o["probation_months"] else f"{o['probation_months']} months") if o.get("probation_months") is not None else "",
        "working_hours": o.get("working_hours") or "", "notice_period": o.get("notice_period") or "",
    }


def render_body(o: dict) -> str:
    from app.services.email import PLACEHOLDER

    ctx = context(o)
    return PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), "")), o.get("body") or DEFAULT_BODY)


def letter_paragraphs(o: dict) -> tuple[str, list[str]]:
    ctx = context(o)
    head = [f"Date: {ctx['date']}", f"Reference: OFR-{str(o['_id'])[-8:].upper()}"]
    return f"Offer of Employment - {o['company_name']}", ["\n".join(head), render_body(o)]


def offer_out(o: dict, with_letter: bool = False) -> dict:
    out = ser({k: v for k, v in o.items() if k not in ("body",)})
    out["template_style"] = o.get("template_style") or DEFAULT_STYLE
    out["style"] = {"key": out["template_style"], **{x: style_of(o)[x] for x in ("name", "primary", "accent", "tint", "head", "serif")}}
    out["has_signature"] = bool(o.get("signature_asset_id"))
    out["salary_text"] = money(o["salary"], o.get("currency", "INR"))
    if with_letter:
        out["letter"] = render_body(o)
        sig = load_signature(o.get("signature_asset_id"))
        if sig:
            import base64

            out["signature_data_url"] = f"data:{'image/png' if sig[:4] == b'\x89PNG' else 'image/jpeg'};base64,{base64.b64encode(sig).decode()}"
    return out


# ---------------------------------------------------------------- assets used by the PDF
MAX_ASSET_BYTES = 1024 * 1024
_CLD_HOST = __import__("re").compile(r"^https://res\.cloudinary\.com/")


def load_logo(url) -> bytes:
    """Logo bytes for the letterhead. Only files that live in OUR storage are read (this server's uploads folder or
    Cloudinary) - never an arbitrary URL, so a recruiter cannot make the server fetch internal addresses."""
    import os

    from app import config
    from app.services import storage

    if not url:
        return None
    try:
        if url.startswith("/uploads/") and ".." not in url:
            path = (config.UPLOAD_DIR / url[len("/uploads/"):]).resolve()
            if str(path).startswith(str(config.UPLOAD_DIR.resolve())) and path.is_file() and path.stat().st_size <= MAX_ASSET_BYTES:
                return path.read_bytes()
        elif _CLD_HOST.match(url) and f"/{config.CLOUDINARY_CLOUD_NAME}/" in url and config.CLOUDINARY_CLOUD_NAME:
            data = storage._get(url, timeout=15)
            return data if len(data) <= MAX_ASSET_BYTES else None
    except Exception:
        return None
    return None


def load_signature(asset_id) -> bytes:
    from app.database import offer_assets_collection
    from app.services import storage

    if not asset_id:
        return None
    try:
        a = offer_assets_collection.find_one({"_id": asset_id})
        return storage.read_private(a.get("storage"), a.get("storage_ref")) if a else None
    except Exception:
        return None


def render_pdf(o: dict) -> bytes:
    from app.services import offer_pdf

    return offer_pdf.build(o, logo=load_logo(o.get("company_logo_url")), signature=load_signature(o.get("signature_asset_id")))
