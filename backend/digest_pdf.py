"""One-page PDF of the daily digest (sent on WhatsApp, attached to the email)."""
from __future__ import annotations

import io
from typing import Optional


def build_digest_pdf(s: dict, image: Optional[bytes] = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"Daily digest {s['date']}")
    st = getSampleStyleSheet()
    cmp = s.get("compare") or {}
    label = cmp.get("label", "last week")
    els = [Paragraph("Vashu Hyundai · Daily digest", st["Title"]),
           Paragraph(f"{s['date']} — compared with {label} ({cmp.get('date', '—')})", st["Normal"]),
           Spacer(1, 8 * mm)]

    rows = [["", s["date"], label.capitalize(), "Change"]]
    for key, name in (("entries", "Entries"), ("exits", "Exits"), ("unique_vehicles", "Unique vehicles")):
        rows.append([name, s.get(key, 0), cmp.get(key, "—"), (s.get("deltas") or {}).get(key, "—")])
    rows += [["Average stay", s.get("avg_dwell", "—"), cmp.get("avg_dwell", "—"), ""],
             ["Longest stay", s.get("max_dwell", "—"), "", ""],
             ["Busiest hour", f"{s.get('busiest_hour') or '—'} ({s.get('busiest_hour_entries', 0)} entries)", "", ""],
             ["Busiest camera", f"{s.get('busiest_camera', '—')} ({s.get('busiest_camera_count', 0)})", "", ""],
             ["Saved automatically", s.get("auto_saved", 0), "", ""],
             ["Sent to review", f"{s.get('sent_to_review', 0)} ({s.get('review_rate', '—')})", "", ""],
             ["Missed entry/exit", s.get("missed_events", 0), "", ""],
             ["Inside a long time", s.get("long_open", 0), "", ""]]
    tbl = Table(rows, colWidths=[48 * mm, 52 * mm, 40 * mm, 30 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563EB")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    els.append(tbl)
    if s.get("long_open_plates"):
        els += [Spacer(1, 4 * mm), Paragraph("Still inside: " + ", ".join(s["long_open_plates"]), st["Normal"])]
    if image:
        try:
            img = Image(io.BytesIO(image))
            w, h = img.imageWidth, img.imageHeight
            scale = min(170 * mm / w, 95 * mm / h, 1.0) if w and h else 1.0
            img.drawWidth, img.drawHeight = w * scale, h * scale
            els += [Spacer(1, 8 * mm), Paragraph(f"First car in the busiest hour ({s.get('busiest_hour')})", st["Heading3"]), img]
        except Exception:
            pass  # a broken snapshot never stops the report
    doc.build(els)
    return buf.getvalue()
