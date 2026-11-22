"""Offer letter templates: 5 styles, branding, signature, A4 PDF with page numbers, access control."""
import io
import struct
import zlib

import pytest
from pypdf import PdfReader

from tests.conftest import JOB

STYLES = ["modern_corporate", "minimal_professional", "premium_corporate", "classic_formal", "modern_gradient"]


def _png(w=60, h=24, rgb=(20, 30, 120)):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@pytest.fixture()
def offer_ctx(recruiter, admin, make_actor):
    job = recruiter.post("/api/recruiter/jobs", json={**JOB}).json()
    admin.patch(f"/api/admin/jobs/{job['id']}/status", json={"status": "approved"})
    cand = make_actor("user", "Bunty Kumar")
    assert cand.post("/api/resumes", files={"file": ("cv.pdf", b"%PDF-1.4 x", "application/pdf")}).status_code == 201
    app_id = cand.post(f"/api/jobs/{job['id']}/apply", json={}).json()["application_id"]
    return recruiter, admin, cand, app_id


def _make(rec, app_id, **extra):
    r = rec.post("/api/recruiter/offers", json={"application_id": app_id, "designation": "Full Stack Developer", "salary": 1500000, "joining_date": "2026-12-15T00:00:00Z", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_five_styles_are_listed(offer_ctx):
    rec = offer_ctx[0]
    items = rec.get("/api/recruiter/offer-styles").json()["items"]
    assert [s["key"] for s in items] == STYLES
    assert {s["name"] for s in items} == {"Modern Corporate", "Minimal Professional", "Premium Corporate", "Classic Formal", "Modern Gradient"}


@pytest.mark.parametrize("style", STYLES)
def test_every_style_renders_a_professional_a4_pdf(offer_ctx, style):
    rec, _, _, app_id = offer_ctx
    sig = rec.post("/api/recruiter/offer-assets/signature", files={"file": ("sig.png", _png(), "image/png")}).json()
    o = _make(rec, app_id, template_style=style, probation_months=3, working_hours="Mon-Fri, 10:00 AM - 7:00 PM", notice_period="30 days",
              company_address="12 MG Road, Jaipur", company_website="https://carevo.example/", company_email="hr@carevo.example", company_phone="+91 99999 00000",
              signatory_name="Asha Verma", signatory_title="Head of People", signature_asset_id=sig["id"], terms="Background verification is mandatory.\nConfidentiality applies.")
    r = rec.get(f"/api/recruiter/offers/{o['id']}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    pdf = PdfReader(io.BytesIO(r.content))
    page = pdf.pages[0]
    assert round(float(page.mediabox.width)) == 595 and round(float(page.mediabox.height)) == 842, "A4"
    text = "\n".join(p.extract_text() for p in pdf.pages)
    for needle in ["Offer of Employment", "Bunty Kumar", "Full Stack Developer", "15,00,000", "15 December 2026", "3 months", "Mon-Fri", "30 days", "Asha Verma", "Head of People",
                   "1. Background verification", "2. Confidentiality", "Candidate acceptance", f"Page 1 of {len(pdf.pages)}"]:
        assert needle in text, f"{style}: missing {needle!r}"


def test_long_letters_flow_onto_more_pages_with_numbering(offer_ctx):
    rec, _, _, app_id = offer_ctx
    terms = "\n".join(f"Clause number {i}: " + "The employee agrees to the policy described in this clause in full. " * 3 for i in range(1, 13))
    o = _make(rec, app_id, terms=terms)
    pdf = PdfReader(io.BytesIO(rec.get(f"/api/recruiter/offers/{o['id']}/pdf").content))
    assert len(pdf.pages) >= 2
    assert f"Page 2 of {len(pdf.pages)}" in pdf.pages[1].extract_text()


def test_default_style_and_prefilled_signatory(offer_ctx):
    rec, _, _, app_id = offer_ctx
    o = _make(rec, app_id)
    assert o["template_style"] == "modern_corporate" and o["signatory_name"] == "Rita Recruiter"


def test_branding_validation(offer_ctx):
    rec, _, _, app_id = offer_ctx
    bad = lambda **kw: rec.post("/api/recruiter/offers", json={"application_id": app_id, "designation": "Dev", "salary": 1, **kw}).status_code
    assert bad(template_style="rainbow") == 422
    assert bad(company_website="javascript:alert(1)") == 422
    assert bad(probation_months=99) == 422
    assert bad(signature_asset_id="a" * 24) == 422, "unknown signature asset"


def test_signature_upload_rules_and_ownership(offer_ctx, make_actor):
    rec, _, cand, app_id = offer_ctx
    up = lambda who, name, data: who.post("/api/recruiter/offer-assets/signature", files={"file": (name, data, "image/png")})
    assert up(rec, "s.gif", b"GIF89a").status_code == 400
    assert up(rec, "s.png", b"not really a png").status_code == 400
    assert up(rec, "s.png", b"\x89PNG" + b"0" * (401 * 1024)).status_code == 400
    assert up(cand, "s.png", _png()).status_code == 403, "candidates cannot upload signatures"
    mine = up(rec, "s.png", _png()).json()
    other = make_actor("recruiter")
    r = other.post("/api/recruiter/offers", json={"application_id": app_id, "designation": "Dev", "salary": 1, "signature_asset_id": mine["id"]})
    assert r.status_code in (403, 404, 422), "another recruiter cannot use my signature (or my application)"


def test_candidate_gets_the_pdf_only_after_it_is_sent_and_only_their_own(offer_ctx, make_actor):
    rec, _, cand, app_id = offer_ctx
    o = _make(rec, app_id, template_style="premium_corporate")
    assert cand.get(f"/api/me/offers/{o['id']}/pdf").status_code == 404, "drafts are hidden"
    assert rec.post(f"/api/recruiter/offers/{o['id']}/send").status_code == 200
    r = cand.get(f"/api/me/offers/{o['id']}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert make_actor("user").get(f"/api/me/offers/{o['id']}/pdf").status_code == 404
    assert make_actor("recruiter").get(f"/api/recruiter/offers/{o['id']}/pdf").status_code == 404
    detail = cand.get(f"/api/me/offers/{o['id']}").json()
    assert detail["style"]["key"] == "premium_corporate" and detail["style"]["primary"] == "#0f172a"


def test_signature_preview_data_is_returned_with_the_offer(offer_ctx):
    rec, _, cand, app_id = offer_ctx
    sig = rec.post("/api/recruiter/offer-assets/signature", files={"file": ("s.png", _png(), "image/png")}).json()
    assert sig["data_url"].startswith("data:image/png;base64,")
    o = _make(rec, app_id, signature_asset_id=sig["id"])
    rec.post(f"/api/recruiter/offers/{o['id']}/send")
    d = cand.get(f"/api/me/offers/{o['id']}").json()
    assert d["has_signature"] is True and d["signature_data_url"].startswith("data:image/png;base64,")


def test_candidate_accepts_and_rejects(offer_ctx):
    rec, _, cand, app_id = offer_ctx
    o = _make(rec, app_id)
    rec.post(f"/api/recruiter/offers/{o['id']}/send")
    r = cand.post(f"/api/me/offers/{o['id']}/respond", json={"decision": "accept", "note": "Thank you"})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert cand.post(f"/api/me/offers/{o['id']}/respond", json={"decision": "decline"}).status_code == 400, "final"
    assert rec.get("/api/recruiter/offers").json()["counts"].get("accepted") == 1


def test_update_draft_style_and_lock_after_send(offer_ctx):
    rec, _, _, app_id = offer_ctx
    o = _make(rec, app_id)
    r = rec.put(f"/api/recruiter/offers/{o['id']}", json={"template_style": "classic_formal", "notice_period": "60 days"})
    assert r.status_code == 200 and r.json()["template_style"] == "classic_formal" and r.json()["notice_period"] == "60 days"
    rec.post(f"/api/recruiter/offers/{o['id']}/send")
    assert rec.put(f"/api/recruiter/offers/{o['id']}", json={"notice_period": "90 days"}).status_code == 400


def test_logo_is_only_read_from_our_own_storage(offer_ctx, monkeypatch):
    from app.services import offers as lib, storage
    calls = []
    monkeypatch.setattr(storage, "_get", lambda *a, **k: calls.append(a) or b"x")
    assert lib.load_logo("http://169.254.169.254/latest/meta-data") is None
    assert lib.load_logo("https://evil.example/logo.png") is None
    assert lib.load_logo("/uploads/../../etc/passwd") is None
    assert calls == [], "no outbound request for foreign URLs"


def test_admin_can_list_and_download_every_offer_but_users_cannot(offer_ctx, admin, make_actor):
    rec, _, cand, app_id = offer_ctx
    o = _make(rec, app_id)
    rec.post(f"/api/recruiter/offers/{o['id']}/send")
    lst = admin.get("/api/admin/offers").json()
    assert lst["total"] == 1 and lst["counts"]["sent"] == 1
    assert admin.get("/api/admin/offers", params={"q": "bunty"}).json()["total"] == 1
    assert admin.get(f"/api/admin/offers/{o['id']}/pdf").content.startswith(b"%PDF")
    assert cand.get("/api/admin/offers").status_code == 403 and rec.get("/api/admin/offers").status_code == 403
