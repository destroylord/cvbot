"""Membaca teks dan font dari file CV (PDF atau DOCX)."""
import logging
import re
from collections import Counter
from pathlib import Path

log = logging.getLogger("cvbot.cv")


def extract_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        text = _pdf_text(path)
    elif ext == ".docx":
        text = _docx_text(path)
    else:
        raise ValueError(f"Format CV tidak didukung: {ext} (pakai PDF atau DOCX)")
    return _clean(text)


def detect_font(path: str) -> str | None:
    """Nama font yang paling banyak dipakai di CV (font isi, bukan judul). None kalau tidak diketahui."""
    try:
        ext = Path(path).suffix.lower()
        counts = _pdf_fonts(path) if ext == ".pdf" else _docx_fonts(path) if ext == ".docx" else Counter()
    except Exception:
        log.info("gagal mendeteksi font CV", exc_info=True)
        return None
    return counts.most_common(1)[0][0] if counts else None


def _open_pdf(path: str):
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")  # banyak PDF "terkunci" tanpa password, hanya dibatasi izin edit
        except Exception as e:
            raise ValueError("PDF CV terkunci password. Export ulang tanpa password.") from e
    return reader


def _pdf_text(path: str) -> str:
    return "\n".join(page.extract_text() or "" for page in _open_pdf(path).pages)


def _pdf_fonts(path: str) -> Counter:
    counts = Counter()

    def visit(text, cm, tm, font_dict, font_size):
        if font_dict and text.strip():
            counts[str(font_dict.get("/BaseFont", ""))] += len(text.strip())

    for page in _open_pdf(path).pages:
        page.extract_text(visitor_text=visit)
    counts.pop("", None)
    return counts


def _docx_fonts(path: str) -> Counter:
    from docx import Document
    doc = Document(path)
    normal = doc.styles["Normal"].font.name or _docx_theme_font(doc)
    paragraphs = list(doc.paragraphs) + [p for t in doc.tables for row in t.rows for c in row.cells
                                         for p in c.paragraphs]
    counts = Counter()
    for para in paragraphs:
        style_font = para.style.font.name if para.style is not None else None
        for run in para.runs:
            name = run.font.name or style_font or normal
            if name and run.text.strip():
                counts[name] += len(run.text.strip())
    if not counts and normal:
        counts[normal] = 1
    return counts


def _docx_theme_font(doc) -> str | None:
    """Font tema Word (Calibri di Word 2007-2021, Aptos di Word terbaru) kalau gaya Normal tidak menyebut font."""
    for part in doc.part.package.iter_parts():
        if str(part.partname).startswith("/word/theme/"):
            match = re.search(rb'<a:minorFont>\s*<a:latin typeface="([^"]+)"', part.blob)
            if match:
                return match.group(1).decode()
    return None


def _docx_text(path: str) -> str:
    from docx import Document
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    # Banyak template CV menaruh isinya di dalam tabel
    for table in doc.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                if cell.text.strip() and cell.text not in cells:  # sel gabungan muncul berulang
                    cells.append(cell.text)
            parts.append("\n".join(cells))
    # Header (biasanya berisi nama & kontak)
    for section in doc.sections:
        parts = [p.text for p in section.header.paragraphs] + parts
    return "\n".join(parts)


def _clean(text: str) -> str:
    text = text.replace("\x00", "").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()
