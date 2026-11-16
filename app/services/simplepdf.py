"""Tiny dependency-free PDF writer (Helvetica text, A4, automatic pages) for offer letters and reports."""
import textwrap


def _esc(s: str) -> str:
    s = s.replace("₹", "Rs. ").replace("–", "-").replace("—", "-").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("•", "-")
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build(title: str, paragraphs: list[str], footer: str = "", header: str = "") -> bytes:
    """paragraphs: plain strings; a string starting with '# ' is rendered as a bold heading."""
    W, H, M = 595, 842, 56
    lines: list[tuple[str, int, bool]] = []  # text, size, bold
    for p in paragraphs:
        if p.startswith("# "):
            lines.append(("", 6, False))
            lines.append((p[2:], 13, True))
            continue
        for raw in p.split("\n"):
            wrapped = textwrap.wrap(raw, 92) or [""]
            lines.extend((w, 10, False) for w in wrapped)
        lines.append(("", 5, False))
    pages, cur, y = [], [], H - M - 40
    for text, size, bold in lines:
        step = size + 4
        if y - step < M + 20:
            pages.append(cur)
            cur, y = [], H - M - 40
        cur.append((y, text, size, bold))
        y -= step
    pages.append(cur)

    objs: list[bytes] = []
    add = lambda b: objs.append(b) or len(objs)
    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"")  # pages placeholder
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    kids = []
    for i, items in enumerate(pages):
        ops = [f"BT /F2 18 Tf {M} {H - M} Td ({_esc(title)}) Tj ET"]
        if header:
            ops.append(f"BT /F1 9 Tf {M} {H - M - 16} Td ({_esc(header)}) Tj ET")
        for y0, text, size, bold in items:
            if text:
                ops.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {M} {y0} Td ({_esc(text)}) Tj ET")
        ops.append(f"BT /F1 8 Tf {M} {M - 14} Td ({_esc(footer)}   Page {i + 1} of {len(pages)}) Tj ET")
        stream = "\n".join(ops).encode("latin-1", "replace")
        c = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        p = add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] /Contents {c} 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>".encode())
        kids.append(p)
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offsets:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
