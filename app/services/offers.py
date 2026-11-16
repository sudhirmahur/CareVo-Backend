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
    out["salary_text"] = money(o["salary"], o.get("currency", "INR"))
    if with_letter:
        out["letter"] = render_body(o)
    return out
