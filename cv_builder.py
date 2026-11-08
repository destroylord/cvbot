"""Membuat PDF CV dari profil (hasil extract_profile / tailor_cv).

Tata letak satu kolom, tanpa tabel atau gambar, supaya mudah dibaca sistem ATS.
"""
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

HEADINGS = {
    "id": {"ringkasan": "Ringkasan", "pengalaman": "Pengalaman Kerja", "proyek": "Proyek", "skill": "Keahlian",
           "pendidikan": "Pendidikan", "sertifikasi": "Sertifikasi", "bahasa": "Bahasa"},
    "en": {"ringkasan": "Summary", "pengalaman": "Work Experience", "proyek": "Projects", "skill": "Skills",
           "pendidikan": "Education", "sertifikasi": "Certifications", "bahasa": "Languages"},
}

# Font standar PDF (Helvetica) hanya mendukung karakter Latin. Karakter lain diganti yang mirip.
REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    "•": "-", "…": "...", " ": " ", "→": "->",
}

ACCENT = colors.HexColor("#1F3A5F")
BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=10, leading=13.5)
STYLES = {
    "name": ParagraphStyle("name", BASE, fontName="Helvetica-Bold", fontSize=20, leading=24,
                           alignment=TA_CENTER, textColor=ACCENT),
    "contact": ParagraphStyle("contact", BASE, fontSize=9, leading=12, alignment=TA_CENTER,
                              textColor=colors.HexColor("#444444")),
    "heading": ParagraphStyle("heading", BASE, fontName="Helvetica-Bold", fontSize=11.5, leading=14,
                              textColor=ACCENT, spaceBefore=9, spaceAfter=2),
    "title": ParagraphStyle("title", BASE, fontName="Helvetica-Bold"),
    "meta": ParagraphStyle("meta", BASE, fontSize=9, textColor=colors.HexColor("#555555"), spaceAfter=1),
    "body": BASE,
}


def _text(value) -> str:
    """Teks aman untuk Paragraph reportlab: karakter diganti/dibuang lalu di-escape."""
    s = "".join(REPLACEMENTS.get(ch, ch) for ch in str(value or "").strip())
    s = s.encode("cp1252", "ignore").decode("cp1252")
    return escape(s)


def _list(value) -> list:
    """AI kadang mengembalikan string atau null untuk field yang seharusnya list."""
    if not value:
        return []
    if isinstance(value, str):
        return [line.strip(" -•\t") for line in value.splitlines() if line.strip(" -•\t")]
    if isinstance(value, list):
        return [v for v in value if v]
    return [value]


def _heading(title: str) -> list:
    return [Paragraph(_text(title).upper(), STYLES["heading"]),
            HRFlowable(width="100%", thickness=0.6, color=ACCENT, spaceBefore=1, spaceAfter=4)]


def _bullets(items: list) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(_text(i), STYLES["body"]), leftIndent=10, value="•") for i in items],
        bulletType="bullet", start="•", leftIndent=10, bulletFontSize=10)


def _entry(title: str, meta: str, points: list) -> KeepTogether:
    parts = [Paragraph(title, STYLES["title"])]
    if meta:
        parts.append(Paragraph(meta, STYLES["meta"]))
    if points:
        parts.append(_bullets(points))
    parts.append(Spacer(1, 5))
    return KeepTogether(parts)


def _story(profile: dict, lang: str) -> list:
    h = HEADINGS.get(lang, HEADINGS["id"])
    story = [Paragraph(_text(profile.get("nama") or "Curriculum Vitae"), STYLES["name"]), Spacer(1, 3)]

    contact = [profile.get("email"), profile.get("telepon"), profile.get("lokasi")] + _list(profile.get("tautan"))
    contact = [_text(c) for c in contact if c]
    if contact:
        story.append(Paragraph(" | ".join(contact), STYLES["contact"]))
    story.append(Spacer(1, 4))

    if profile.get("ringkasan"):
        story += _heading(h["ringkasan"]) + [Paragraph(_text(profile["ringkasan"]), STYLES["body"])]

    experiences = [e for e in _list(profile.get("pengalaman")) if isinstance(e, dict)]
    if experiences:
        story += _heading(h["pengalaman"])
        for exp in experiences:
            title = _text(exp.get("posisi"))
            if exp.get("perusahaan"):
                title += f" - {_text(exp['perusahaan'])}" if title else _text(exp["perusahaan"])
            story.append(_entry(title, _text(exp.get("periode")), _list(exp.get("poin"))))

    projects = [p for p in _list(profile.get("proyek")) if isinstance(p, dict)]
    if projects:
        story += _heading(h["proyek"])
        for proj in projects:
            story.append(_entry(_text(proj.get("nama")), "", _list(proj.get("poin"))))

    skills = _list(profile.get("skill"))
    if skills:
        story += _heading(h["skill"]) + [Paragraph(", ".join(_text(s) for s in skills), STYLES["body"])]

    for key in ("pendidikan", "sertifikasi", "bahasa"):
        items = _list(profile.get(key))
        if items:
            story += _heading(h[key]) + [_bullets(items)]
    return story


def build_pdf(profile: dict, path: str, lang: str | None = "id") -> None:
    doc = SimpleDocTemplate(
        path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"CV {profile.get('nama') or ''}".strip(), author=profile.get("nama") or "")
    doc.build(_story(profile, "en" if lang == "en" else "id"))
