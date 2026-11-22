"""Professional offer-letter PDFs (A4) in five visual styles, built with ReportLab.

Layout: letterhead (logo + company details) on every page, page numbers "Page X of Y", a summary table,
numbered terms, signatory block with optional signature image and a candidate acceptance block.
Falls back to the tiny built-in writer when ReportLab is not installed, so offers never become undownloadable.
"""
import io
from datetime import datetime

from app.services import offers as offer_lib

try:  # pragma: no cover - import guard
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import BaseDocTemplate, Frame, Image, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

    HAVE_REPORTLAB = True
except Exception:  # pragma: no cover
    HAVE_REPORTLAB = False

W, H = (595.27, 841.89)
MARGIN_X = 20 * 1  # points are set below once mm is known


def _t(s) -> str:
    """Standard PDF fonts have no rupee/euro glyphs or curly quotes: keep text safe."""
    s = str(s if s is not None else "")
    for a, b in (("₹", "Rs. "), ("€", "EUR "), ("£", "GBP "), ("–", "-"), ("—", "-"), ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("•", "-")):
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def _esc(s) -> str:
    return _t(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _hex(c: str):
    return colors.HexColor(c)


def build(o: dict, logo: bytes = None, signature: bytes = None) -> bytes:
    if not HAVE_REPORTLAB:
        from app.services import simplepdf

        title, paras = offer_lib.letter_paragraphs(o)
        return simplepdf.build(title, paras, footer=o.get("company_name", ""))

    st = offer_lib.style_of(o)
    ctx = offer_lib.context(o)
    serif = st["serif"]
    base, bold = ("Times-Roman", "Times-Bold") if serif else ("Helvetica", "Helvetica-Bold")
    primary, accent, ink = _hex(st["primary"]), _hex(st["accent"]), _hex("#1f2937")
    muted = _hex("#6b7280")
    company = o.get("company_name") or "Company"
    ref = f"OFR-{str(o['_id'])[-8:].upper()}"

    body = ParagraphStyle("body", fontName=base, fontSize=10.5 if not serif else 11, leading=15.5, textColor=ink, alignment=TA_JUSTIFY, spaceAfter=7)
    small = ParagraphStyle("small", parent=body, fontSize=9, leading=12.5, textColor=muted, alignment=TA_LEFT, spaceAfter=2)
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=19 if st["head"] != "classic" else 17, leading=23, textColor=primary, alignment=TA_CENTER if st["head"] == "classic" else TA_LEFT, spaceAfter=4)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=11.5, leading=15, textColor=primary, spaceBefore=10, spaceAfter=5)
    cell = ParagraphStyle("cell", parent=body, alignment=TA_LEFT, spaceAfter=0, fontSize=10, leading=13.5)
    cell_b = ParagraphStyle("cellb", parent=cell, fontName=bold, textColor=primary)

    def details_lines():
        out = [o.get(k) for k in ("company_address", "company_website", "company_email", "company_phone")]
        return [_t(x) for x in out if x]

    def draw_logo(c, x, y, max_w, max_h):
        if not logo:
            return 0
        try:
            img = ImageReader(io.BytesIO(logo))
            iw, ih = img.getSize()
            k = min(max_w / iw, max_h / ih)
            c.drawImage(img, x, y - ih * k, iw * k, ih * k, mask="auto")
            return iw * k
        except Exception:
            return 0

    LM, RM, TOP = 20 * mm, 20 * mm, 40 * mm

    def header(c, doc):
        c.saveState()
        first = doc.page == 1
        top = H
        kind = st["head"]
        if kind == "band":
            c.setFillColor(primary); c.rect(0, H - 30 * mm, W, 30 * mm, stroke=0, fill=1)
            c.setFillColor(accent); c.rect(0, H - 31.5 * mm, W, 1.5 * mm, stroke=0, fill=1)
            tx = LM + draw_logo_white(c, LM, H - 7 * mm, 38 * mm, 15 * mm)
            c.setFillColor(colors.white); c.setFont(bold, 16 if first else 12); c.drawString(tx + (4 * mm if tx > LM else 0), H - 16 * mm, _t(company))
            if first:
                c.setFont(base, 8); yy = H - 22 * mm
                for ln in details_lines()[:2]:
                    c.drawString(tx + (4 * mm if tx > LM else 0), yy, ln); yy -= 3.6 * mm
        elif kind == "gradient":
            clip = c.beginPath(); clip.rect(0, H - 30 * mm, W, 30 * mm)
            c.saveState(); c.clipPath(clip, stroke=0, fill=0)
            c.linearGradient(0, H - 30 * mm, W, H, (primary, accent), extend=False)
            c.restoreState()
            tx = LM + draw_logo_white(c, LM, H - 7 * mm, 38 * mm, 15 * mm)
            c.setFillColor(colors.white); c.setFont(bold, 16 if first else 12); c.drawString(tx + (4 * mm if tx > LM else 0), H - 16 * mm, _t(company))
            if first:
                c.setFont(base, 8); yy = H - 22 * mm
                for ln in details_lines()[:2]:
                    c.drawString(tx + (4 * mm if tx > LM else 0), yy, ln); yy -= 3.6 * mm
        elif kind == "premium":
            c.setFillColor(primary); c.rect(0, H - 34 * mm, W, 34 * mm, stroke=0, fill=1)
            c.setStrokeColor(accent); c.setLineWidth(1.2); c.line(LM, H - 36 * mm, W - RM, H - 36 * mm); c.setLineWidth(0.4); c.line(LM, H - 37.6 * mm, W - RM, H - 37.6 * mm)
            tx = LM + draw_logo_white(c, LM, H - 8 * mm, 38 * mm, 16 * mm)
            c.setFillColor(colors.white); c.setFont("Times-Bold", 18 if first else 13); c.drawString(tx + (4 * mm if tx > LM else 0), H - 18 * mm, _t(company).upper())
            if first:
                c.setFillColor(accent); c.setFont("Times-Italic", 8.5); yy = H - 24.5 * mm
                for ln in details_lines()[:2]:
                    c.drawString(tx + (4 * mm if tx > LM else 0), yy, ln); yy -= 3.8 * mm
        elif kind == "classic":
            lw = draw_logo(c, (W - 30 * mm) / 2, H - 9 * mm, 30 * mm, 14 * mm) if first else 0
            c.setFillColor(primary); c.setFont("Times-Bold", 17 if first else 12); c.drawCentredString(W / 2, H - (27 if lw else 18) * mm if first else H - 14 * mm, _t(company).upper())
            if first:
                c.setFont("Times-Roman", 8.5); c.setFillColor(muted)
                c.drawCentredString(W / 2, H - (31.5 if lw else 22.5) * mm, "  |  ".join(details_lines()[:3]))
            c.setStrokeColor(primary); c.setLineWidth(1.3); c.line(LM, H - 35 * mm, W - RM, H - 35 * mm); c.setLineWidth(0.4); c.line(LM, H - 36.2 * mm, W - RM, H - 36.2 * mm)
        else:  # minimal
            tx = LM + draw_logo(c, LM, H - 10 * mm, 34 * mm, 13 * mm)
            c.setFillColor(primary); c.setFont(bold, 13 if first else 11); c.drawString(tx + (3 * mm if tx > LM else 0), H - 17 * mm, _t(company))
            if first:
                c.setFillColor(muted); c.setFont(base, 8); yy = H - 22 * mm
                for ln in details_lines()[:3]:
                    c.drawString(tx + (3 * mm if tx > LM else 0), yy, ln); yy -= 3.5 * mm
            c.setStrokeColor(accent); c.setLineWidth(0.6); c.line(LM, H - 33 * mm, W - RM, H - 33 * mm)
        c.restoreState()

    def draw_logo_white(c, x, y, mw, mh):
        # a logo sits on a coloured band: give it a white rounded plate so dark logos stay readable
        if not logo:
            return 0
        try:
            img = ImageReader(io.BytesIO(logo)); iw, ih = img.getSize(); k = min(mw / iw, mh / ih)
            w, h = iw * k, ih * k
            c.setFillColor(colors.white); c.roundRect(x - 1.5 * mm, y - h - 1.5 * mm, w + 3 * mm, h + 3 * mm, 1.5 * mm, stroke=0, fill=1)
            c.drawImage(img, x, y - h, w, h, mask="auto")
            return w + 3 * mm
        except Exception:
            return 0

    class Numbered(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self._footer(total)
                super().showPage()
            super().save()

        def _footer(self, total):
            self.saveState()
            self.setStrokeColor(_hex("#d1d5db")); self.setLineWidth(0.5); self.line(LM, 15 * mm, W - RM, 15 * mm)
            self.setFillColor(muted); self.setFont(base, 8)
            self.drawString(LM, 10.5 * mm, _t(f"{company}  |  Confidential - offer of employment  |  {ref}"))
            self.drawRightString(W - RM, 10.5 * mm, f"Page {self._pageNumber} of {total}")
            self.restoreState()

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=LM, rightMargin=RM, topMargin=TOP, bottomMargin=22 * mm, title=_t(f"Offer of Employment - {o['candidate_name']}"), author=_t(company), subject="Offer of Employment")
    frame = Frame(LM, 22 * mm, W - LM - RM, H - TOP - 22 * mm, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=header)])

    P = lambda text, style=body: Paragraph(text, style)
    story = []
    story.append(P("Offer of Employment", h1))
    meta = Table([[P(f"<b>Date:</b> {_esc(ctx['date'])}", small), P(f"<b>Reference:</b> {ref}", ParagraphStyle('r', parent=small, alignment=2))]], colWidths=[(W - LM - RM) / 2] * 2)
    meta.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [meta, P(f"Dear {_esc(o['candidate_name'])},", body)]

    custom = o.get("body")
    if custom:
        for para in [p for p in offer_lib.render_body(o).split("\n\n") if p.strip()]:
            story.append(P(_esc(para).replace("\n", "<br/>")))
    else:
        story.append(P(f"We are delighted to offer you the position of <b>{_esc(o['designation'])}</b> at <b>{_esc(company)}</b>. We were impressed by your skills and experience, and we are excited about the contribution you will make to our team."))
        story.append(P("The key terms of this offer are summarised below. Please read the terms and conditions that follow carefully."))

    rows = [("Designation", o["designation"]), ("Annual compensation (CTC)", ctx["salary"]), ("Date of joining", ctx["joining_date"])]
    if o.get("probation_months") is not None:
        rows.append(("Probation period", "None" if not o["probation_months"] else f"{o['probation_months']} month{'s' if o['probation_months'] != 1 else ''}"))
    if o.get("working_hours"):
        rows.append(("Working hours", o["working_hours"]))
    if o.get("notice_period"):
        rows.append(("Notice period", o["notice_period"]))
    rows.append(("Offer valid until", ctx["expires"]))
    tbl = Table([[P(_esc(k), cell_b), P(_esc(v), cell)] for k, v in rows], colWidths=[58 * mm, W - LM - RM - 58 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), _hex(st["tint"])),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, _hex("#d1d5db")),
        ("LINEABOVE", (0, 0), (-1, 0), 1.2, primary),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [Spacer(1, 4), tbl, P("Terms and conditions", h2)]
    terms = [t.strip() for t in (o.get("terms") or offer_lib.DEFAULT_TERMS).replace("\r", "").split("\n") if t.strip()]
    for i, t in enumerate(terms, 1):
        story.append(P(f"{i}. {_esc(t.lstrip('0123456789.) ').strip() or t)}" if len(terms) > 1 else _esc(t)))
    story.append(P("Please confirm your acceptance of this offer on CareVo before the validity date mentioned above. We look forward to welcoming you on board."))

    sig_cells = []
    if signature:
        try:
            im = ImageReader(io.BytesIO(signature)); iw, ih = im.getSize(); k = min(46 * mm / iw, 18 * mm / ih)
            sig_cells.append(Image(io.BytesIO(signature), width=iw * k, height=ih * k, hAlign="LEFT"))
        except Exception:
            sig_cells.append(Spacer(1, 14 * mm))
    else:
        sig_cells.append(Spacer(1, 14 * mm))
    signer = o.get("signatory_name") or o.get("recruiter_name") or ""
    left = [P("Yours sincerely,", ParagraphStyle("ys", parent=body, spaceAfter=2, alignment=TA_LEFT)), *sig_cells,
            P(f"<b>{_esc(signer)}</b>", ParagraphStyle("sn", parent=body, spaceAfter=0, alignment=TA_LEFT))]
    if o.get("signatory_title"):
        left.append(P(_esc(o["signatory_title"]), small))
    left.append(P(_esc(company), small))
    right = [P("Candidate acceptance", ParagraphStyle("ca", parent=body, fontName=bold, textColor=primary, spaceAfter=2, alignment=TA_LEFT)),
             P("I accept this offer and the terms stated above.", small), Spacer(1, 10 * mm),
             P("______________________________", small), P(f"{_esc(o['candidate_name'])}", small), P("Date: ____________________", small)]
    sig = Table([[left, right]], colWidths=[(W - LM - RM) / 2] * 2)
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 8), KeepTogether([sig])]

    doc.build(story, canvasmaker=Numbered)
    return buf.getvalue()
