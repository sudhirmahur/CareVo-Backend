"""Professional offer-letter PDFs (A4) in seven visual styles, built with ReportLab.

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
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import BaseDocTemplate, Flowable, Frame, Image, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

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


# ------------------------------------------------------------------ decorative artwork (pure vector, no images)
DARK = "#1f1f24"


def _poly(c, pts, color):
    """pts: ('M',x,y) ('L',x,y) ('C',x1,y1,x2,y2,x3,y3) in mm."""
    p = c.beginPath()
    for seg in pts:
        v = [a * mm for a in seg[1:]]
        if seg[0] == "M":
            p.moveTo(*v)
        elif seg[0] == "L":
            p.lineTo(*v)
        else:
            p.curveTo(*v)
    p.close()
    c.setFillColor(_hex(color)); c.drawPath(p, stroke=0, fill=1)


def swoosh_top(c, red, orange):
    w = W / mm; h = H / mm
    # top-left: orange sweep, red sweep over it
    _poly(c, [("M", 0, h), ("L", 0, h - 40), ("C", 6, h - 24, 22, h - 8, 62, h)], orange)
    _poly(c, [("M", 0, h), ("L", 0, h - 25), ("C", 5, h - 14, 14, h - 5, 34, h)], red)
    _poly(c, [("M", 36, h), ("L", 46, h), ("C", 38, h - 3, 30, h - 8, 24, h - 13), ("C", 28, h - 8, 31, h - 3, 36, h)], DARK)
    # top-right: dark corner, red and orange stripes
    _poly(c, [("M", w - 52, h), ("L", w, h), ("L", w, h - 30), ("C", w - 14, h - 22, w - 34, h - 10, w - 52, h)], DARK)
    _poly(c, [("M", w - 70, h), ("L", w - 54, h), ("C", w - 36, h - 11, w - 16, h - 24, w, h - 32), ("L", w, h - 38), ("C", w - 20, h - 28, w - 46, h - 12, w - 70, h)], red)
    _poly(c, [("M", w - 90, h), ("L", w - 72, h), ("C", w - 52, h - 12, w - 28, h - 26, w, h - 40), ("L", w, h - 43), ("C", w - 30, h - 29, w - 62, h - 12, w - 90, h)], orange)


def swoosh_bottom(c, red, orange):
    w = W / mm
    _poly(c, [("M", w, 0), ("L", w, 34), ("C", w - 18, 22, w - 46, 9, w - 78, 0)], DARK)
    _poly(c, [("M", w - 50, 0), ("C", w - 34, 6, w - 14, 15, w, 22), ("L", w, 28), ("C", w - 20, 17, w - 44, 7, w - 66, 0)], red)
    _poly(c, [("M", w - 70, 0), ("C", w - 46, 7, w - 20, 19, w, 31), ("L", w, 36), ("C", w - 24, 22, w - 52, 9, w - 82, 0)], orange)


def circles(c, spec):
    c.saveState()
    for cx, cy, r, color, a in spec:
        c.setFillColor(_hex(color)); c.setFillAlpha(a)
        c.circle(cx * mm, cy * mm, r * mm, stroke=0, fill=1)
    c.restoreState()


def circles_top():
    w = W / mm; h = H / mm
    return [(w - 6, h - 4, 19, "#22b8cf", 1), (w - 40, h - 4, 7, "#fbbf24", 1), (w - 24, h - 20, 10, "#67e8f9", .75), (w - 3, h - 36, 7, "#ef4444", 1), (w - 56, h - 10, 4, "#f97316", 1)]


def circles_bottom():
    return [(2, 12, 15, "#f97316", 1), (20, 2, 7, "#22b8cf", 1), (14, 31, 5, "#fbbf24", 1), (30, 9, 3.5, "#ef4444", 1), (4, 34, 3, "#67e8f9", .8)]


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

    kind0 = st["head"]
    LM, RM, TOP = 20 * mm, 20 * mm, {"swoosh": 50 * mm, "circles": 44 * mm}.get(kind0, 40 * mm)

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
        elif kind == "swoosh":
            swoosh_top(c, st["primary"], st["accent"])
            lw = draw_logo(c, LM, H - 14 * mm, 40 * mm, 20 * mm)
            if not lw:
                nm = _t(company); size = 20
                while stringWidth(nm, "Helvetica-Bold", size) > 62 * mm and size > 9:
                    size -= 1
                c.setFillColor(_hex(DARK)); c.setFont("Helvetica-Bold", size); c.drawString(LM, H - 24 * mm, nm)
            c.setStrokeColor(_hex("#9ca3af")); c.setLineWidth(0.6); c.line(100 * mm, H - 12 * mm, 100 * mm, H - 36 * mm)
            c.setFillColor(_hex(DARK)); c.setFont("Helvetica-Bold", 14); c.drawString(105 * mm, H - 17 * mm, _t(company)[:34])
            c.setFont("Helvetica", 8.5); c.setFillColor(_hex("#374151")); yy = H - 23 * mm
            import textwrap

            wrapped = [w for ln in details_lines() for w in textwrap.wrap(ln, 38)][:5]
            for ln in wrapped:
                c.drawString(105 * mm, yy, ln); yy -= 3.9 * mm
            c.setStrokeColor(_hex(DARK)); c.setLineWidth(0.9); c.line(LM, H - 41 * mm, W - RM, H - 41 * mm)
            c.setStrokeColor(_hex(st["accent"])); c.setLineWidth(1.4); c.line(W / 2, H - 41 * mm, W - RM, H - 41 * mm)
        elif kind == "circles":
            circles(c, circles_top())
            lw = draw_logo(c, LM, H - 8 * mm, 34 * mm, 13 * mm)
            nx = LM + (lw + 3 * mm if lw else 0)
            c.setFillColor(_hex(DARK)); c.setFont("Helvetica-Bold", 13 if first else 11); c.drawString(nx, H - 16 * mm, _t(company)[:36])
            c.setFont("Helvetica", 8.2); c.setFillColor(_hex("#4b5563")); yy = H - 27 * mm
            for ln in details_lines()[:4]:
                c.drawString(LM, yy, ln[:60]); yy -= 3.6 * mm
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
            if kind0 == "swoosh":
                swoosh_bottom(self, st["primary"], st["accent"])
                self.setStrokeColor(_hex("#6b7280")); self.setLineWidth(0.6); self.line(LM, 17 * mm, W - 85 * mm, 17 * mm)
                self.setFillColor(_hex(DARK)); self.setFont("Helvetica-Bold", 10); self.drawString(LM, 11 * mm, _t(company)[:32])
                self.setFont("Helvetica", 7.5); self.setFillColor(muted); self.drawString(LM, 7 * mm, _t(f"Confidential - offer of employment | {ref}"))
                self.setFont("Helvetica", 8.5); self.drawString(W - 140 * mm, 11 * mm, f"Page {self._pageNumber} of {total}")
                self.restoreState(); return
            if kind0 == "circles":
                circles(self, circles_bottom())
                self.setStrokeColor(_hex("#d1d5db")); self.setLineWidth(0.5); self.line(LM + 14 * mm, 15 * mm, W - RM, 15 * mm)
                self.setFillColor(muted); self.setFont("Helvetica", 8)
                self.drawString(LM + 14 * mm, 10.5 * mm, _t(f"{company}  |  Confidential - offer of employment  |  {ref}"))
                self.drawRightString(W - RM, 10.5 * mm, f"Page {self._pageNumber} of {total}")
                self.restoreState(); return
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
    if kind0 == "swoosh":
        CW = W - LM - RM
        red, orange, dark = primary, accent, _hex(DARK)
        tint = _hex(st["tint"])

        class Boxed(Flowable):
            def __init__(self, inner, fill, pad=4 * mm, r=3.5 * mm):
                super().__init__(); self.inner, self.fill, self.pad, self.r = inner, fill, pad, r

            def wrap(self, aw, ah):
                w, h = self.inner.wrap(CW - 2 * self.pad, ah)
                self.h = h + 2 * self.pad
                return CW, self.h

            def draw(self):
                self.canv.setFillColor(self.fill); self.canv.roundRect(0, 0, CW, self.h, self.r, stroke=0, fill=1)
                self.inner.drawOn(self.canv, self.pad, self.pad)

        class Pill(Flowable):
            def __init__(self, text, pw, ph=11 * mm, line=True, center=True):
                super().__init__(); self.text, self.pw, self.ph, self.line, self.center = text, pw, ph, line, center

            def wrap(self, aw, ah):
                return CW, self.ph + 2 * mm

            def draw(self):
                c = self.canv; x0 = (CW - self.pw) / 2 if self.center else 0
                if self.line:
                    c.setStrokeColor(orange); c.setLineWidth(0.8)
                    if self.center:
                        c.line(0, self.ph / 2 + 1 * mm, x0 - 3 * mm, self.ph / 2 + 1 * mm)
                    c.line(x0 + self.pw + 3 * mm, self.ph / 2 + 1 * mm, CW, self.ph / 2 + 1 * mm)
                c.saveState()
                path = c.beginPath(); path.roundRect(x0, 1 * mm, self.pw, self.ph, self.ph / 2.6)
                c.clipPath(path, stroke=0, fill=0)
                c.linearGradient(x0, 0, x0 + self.pw, 0, (red, orange), extend=False)
                c.restoreState()
                c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 13.5 if self.center else 11.5)
                c.drawCentredString(x0 + self.pw / 2, 1 * mm + self.ph / 2 - 4.4, self.text)

        class Num(Flowable):
            def __init__(self, n):
                super().__init__(); self.n = n

            def wrap(self, aw, ah):
                return 9 * mm, 8 * mm

            def draw(self):
                c = self.canv; c.setFillColor(tint); c.circle(4 * mm, 3.2 * mm, 3.6 * mm, stroke=0, fill=1)
                c.setFillColor(red); c.setFont("Helvetica-Bold", 10); c.drawCentredString(4 * mm, 1.8 * mm, str(self.n))

        big = ParagraphStyle("big", parent=body, fontSize=11.5, leading=17, alignment=TA_LEFT, spaceAfter=6)
        lab = ParagraphStyle("lab", parent=small, fontSize=9.5, textColor=_hex("#374151"))
        val = ParagraphStyle("val", parent=body, fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=dark, alignment=TA_LEFT, spaceAfter=0)
        story = [P(f"<b>Date:</b> {_esc(ctx['date'])}", big), P(f"<b>Reference:</b> {ref}", big), Spacer(1, 2 * mm), Pill("Offer of Employment", 66 * mm), Spacer(1, 2 * mm),
                 P(f"Dear <b>{_esc(o['candidate_name'])}</b>,", big)]
        custom = o.get("body")
        if custom:
            for para in [x for x in offer_lib.render_body(o).split("\n\n") if x.strip()]:
                story.append(P(_esc(para).replace("\n", "<br/>")))
        else:
            story.append(P(f"We are delighted to offer you the position of <b>{_esc(o['designation'])}</b> at <b>{_esc(company)}</b>. We were impressed by your skills and experience and look forward to the contribution you will make to our team.", big))
        cards = Table([[[P("Annual Compensation", lab), P(_esc(ctx["salary"]), val)], [P("Expected Joining Date", lab), P(_esc(ctx["joining_date"]), val)]]], colWidths=[(CW - 8 * mm) / 2] * 2)
        cards.setStyle(TableStyle([("LINEAFTER", (0, 0), (0, 0), 0.7, _hex("#9ca3af")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        story += [Spacer(1, 2 * mm), Boxed(cards, tint), Spacer(1, 5 * mm), Pill("Terms and Conditions:", 56 * mm, 9.5 * mm, center=False), Spacer(1, 2 * mm)]
        lines = []
        if o.get("probation_months") is not None:
            lines.append("No probation period." if not o["probation_months"] else f"Probation period of {o['probation_months']} month{'s' if o['probation_months'] != 1 else ''} from the joining date.")
        if o.get("working_hours"):
            lines.append(f"Working hours: {o['working_hours']}.".replace("..", "."))
        if o.get("notice_period"):
            lines.append(f"Notice period: {o['notice_period']}.".replace("..", "."))
        lines += [t.strip().lstrip("0123456789.) ").strip() or t.strip() for t in (o.get("terms") or offer_lib.DEFAULT_TERMS).replace("\r", "").split("\n") if t.strip()]
        rows_t = Table([[Num(i), P(_esc(t), ParagraphStyle("tt", parent=body, fontSize=11, leading=15, alignment=TA_LEFT, spaceAfter=0))] for i, t in enumerate(lines, 1)], colWidths=[11 * mm, CW - 11 * mm])
        rows_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        notice = P(f"Please respond to this offer on <font color='{st['primary']}'><b>CareVo</b></font> by <font color='{st['primary']}'><b>{_esc(ctx['expires'])}</b></font>.", ParagraphStyle("nt", parent=body, fontSize=12, alignment=TA_LEFT, spaceAfter=0))
        story += [rows_t, Spacer(1, 5 * mm), Boxed(notice, tint), Spacer(1, 6 * mm)]
        sigc = []
        if signature:
            try:
                im = ImageReader(io.BytesIO(signature)); iw, ih = im.getSize(); k = min(50 * mm / iw, 20 * mm / ih)
                sigc.append(Image(io.BytesIO(signature), width=iw * k, height=ih * k, hAlign="LEFT"))
            except Exception:
                sigc.append(Spacer(1, 14 * mm))
        else:
            sigc.append(Spacer(1, 14 * mm))
        signer = o.get("signatory_name") or o.get("recruiter_name") or ""
        left = [P("Warm regards,", ParagraphStyle("wr", parent=big, spaceAfter=2)), *sigc, P(f"<b>{_esc(signer)}</b>", ParagraphStyle("sn2", parent=big, fontSize=12.5, spaceAfter=0))]
        if o.get("signatory_title"):
            left.append(P(_esc(o["signatory_title"]), ParagraphStyle("st2", parent=big, spaceAfter=0)))
        left.append(P(_esc(company), ParagraphStyle("co2", parent=big, spaceAfter=0)))
        right = [P("Candidate acceptance", ParagraphStyle("ca2", parent=body, fontName=bold, textColor=red, spaceAfter=2, alignment=TA_LEFT)),
                 P("I accept this offer and the terms stated above.", small), Spacer(1, 10 * mm), P("______________________________", small), P(_esc(o["candidate_name"]), small), P("Date: ____________________", small)]
        sg = Table([[left, right]], colWidths=[CW * 0.55, CW * 0.45])
        sg.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
        story.append(KeepTogether([sg]))
        doc.build(story, canvasmaker=Numbered)
        return buf.getvalue()

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
