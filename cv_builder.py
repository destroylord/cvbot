"""Membuat PDF CV dari profil (hasil extract_profile / tailor_cv).

Tata letak satu kolom, tanpa tabel atau gambar, supaya mudah dibaca sistem ATS.
Font bisa dipilih (lihat cv_fonts.py); default mengikuti font CV asli.
"""
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

import cv_fonts

HEADINGS = {
    "id": {"ringkasan": "Ringkasan", "pengalaman": "Pengalaman Kerja", "proyek": "Proyek", "skill": "Keahlian",
           "pendidikan": "Pendidikan", "sertifikasi": "Sertifikasi", "bahasa": "Bahasa"},
    "en": {"ringkasan": "Summary", "pengalaman": "Work Experience", "proyek": "Projects", "skill": "Skills",
           "pendidikan": "Education", "sertifikasi": "Certifications", "bahasa": "Languages"},
}

# Diganti kalau font-nya tidak punya karakter tersebut
REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    "•": "-", "…": "...", " ": " ", "→": "->",
}

ACCENT = colors.HexColor("#1F3A5F")


def _list(value) -> list:
    """AI kadang mengembalikan string atau null untuk field yang seharusnya list."""
    if not value:
        return []
    if isinstance(value, str):
        return [line.strip(" -•\t") for line in value.splitlines() if line.strip(" -•\t")]
    if isinstance(value, list):
        return [v for v in value if v]
    return [value]


class _Writer:
    def __init__(self, regular: str, bold: str):
        self.regular = regular
        base = ParagraphStyle("base", fontName=regular, fontSize=10, leading=13.5)
        self.styles = {
            "name": ParagraphStyle("name", base, fontName=bold, fontSize=20, leading=24,
                                   alignment=TA_CENTER, textColor=ACCENT),
            "contact": ParagraphStyle("contact", base, fontSize=9, leading=12, alignment=TA_CENTER,
                                      textColor=colors.HexColor("#444444")),
            "heading": ParagraphStyle("heading", base, fontName=bold, fontSize=11.5, leading=14,
                                      textColor=ACCENT, spaceBefore=9, spaceAfter=2),
            "title": ParagraphStyle("title", base, fontName=bold),
            "meta": ParagraphStyle("meta", base, fontSize=9, textColor=colors.HexColor("#555555"), spaceAfter=1),
            "body": base,
        }

    def text(self, value) -> str:
        """Teks aman untuk Paragraph: karakter yang tidak ada di font diganti/dibuang, lalu di-escape."""
        out = []
        for ch in str(value or "").strip():
            if cv_fonts.supports(self.regular, ch):
                out.append(ch)
            elif ch in REPLACEMENTS:
                out.append(REPLACEMENTS[ch])
        return escape("".join(out))

    def heading(self, title: str) -> list:
        return [Paragraph(self.text(title).upper(), self.styles["heading"]),
                HRFlowable(width="100%", thickness=0.6, color=ACCENT, spaceBefore=1, spaceAfter=4)]

    def bullets(self, items: list) -> ListFlowable:
        return ListFlowable(
            [ListItem(Paragraph(self.text(i), self.styles["body"]), leftIndent=10, value="•") for i in items],
            bulletType="bullet", start="•", leftIndent=10, bulletFontSize=10)

    def entry(self, title: str, meta: str, points: list) -> KeepTogether:
        parts = [Paragraph(title, self.styles["title"])]
        if meta:
            parts.append(Paragraph(meta, self.styles["meta"]))
        if points:
            parts.append(self.bullets(points))
        parts.append(Spacer(1, 5))
        return KeepTogether(parts)

    def story(self, profile: dict, lang: str) -> list:
        h, t, s = HEADINGS.get(lang, HEADINGS["id"]), self.text, self.styles
        story = [Paragraph(t(profile.get("nama") or "Curriculum Vitae"), s["name"]), Spacer(1, 3)]

        contact = [profile.get("email"), profile.get("telepon"), profile.get("lokasi")] + _list(profile.get("tautan"))
        contact = [t(c) for c in contact if c]
        if contact:
            story.append(Paragraph(" | ".join(contact), s["contact"]))
        story.append(Spacer(1, 4))

        if profile.get("ringkasan"):
            story += self.heading(h["ringkasan"]) + [Paragraph(t(profile["ringkasan"]), s["body"])]

        experiences = [e for e in _list(profile.get("pengalaman")) if isinstance(e, dict)]
        if experiences:
            story += self.heading(h["pengalaman"])
            for exp in experiences:
                title = t(exp.get("posisi"))
                if exp.get("perusahaan"):
                    title += f" - {t(exp['perusahaan'])}" if title else t(exp["perusahaan"])
                story.append(self.entry(title, t(exp.get("periode")), _list(exp.get("poin"))))

        projects = [p for p in _list(profile.get("proyek")) if isinstance(p, dict)]
        if projects:
            story += self.heading(h["proyek"])
            for proj in projects:
                story.append(self.entry(t(proj.get("nama")), "", _list(proj.get("poin"))))

        skills = _list(profile.get("skill"))
        if skills:
            story += self.heading(h["skill"]) + [Paragraph(", ".join(t(x) for x in skills), s["body"])]

        for key in ("pendidikan", "sertifikasi", "bahasa"):
            items = _list(profile.get(key))
            if items:
                story += self.heading(h[key]) + [self.bullets(items)]
        return story


def build_pdf(profile: dict, path: str, lang: str | None = "id", font: str = cv_fonts.BUILTIN) -> str:
    """Membuat PDF. Mengembalikan keluarga font yang benar-benar dipakai (Helvetica kalau font gagal diambil)."""
    regular, bold, ok = cv_fonts.ensure(font)
    doc = SimpleDocTemplate(
        path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"CV {profile.get('nama') or ''}".strip(), author=profile.get("nama") or "")
    doc.build(_Writer(regular, bold).story(profile, "en" if lang == "en" else "id"))
    return font if ok else cv_fonts.BUILTIN
