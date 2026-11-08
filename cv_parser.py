"""Membaca teks dari file CV (PDF atau DOCX)."""
import re
from pathlib import Path


def extract_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        text = _pdf_text(path)
    elif ext == ".docx":
        text = _docx_text(path)
    else:
        raise ValueError(f"Format CV tidak didukung: {ext} (pakai PDF atau DOCX)")
    return _clean(text)


def _pdf_text(path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")  # banyak PDF "terkunci" tanpa password, hanya dibatasi izin edit
        except Exception as e:
            raise ValueError("PDF CV terkunci password. Export ulang tanpa password.") from e
    return "\n".join(page.extract_text() or "" for page in reader.pages)


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
