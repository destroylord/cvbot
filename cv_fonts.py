"""Font untuk PDF CV yang disesuaikan.

Font diambil dari Google Fonts (sekali saja, disimpan di data/fonts/). Font Microsoft yang umum di CV
diganti padanannya yang ukurannya identik (metric-compatible), jadi tata letaknya tetap sama.
Kalau font tidak bisa diambil (misalnya offline), PDF memakai Helvetica bawaan.
"""
import logging
import re
from pathlib import Path

import requests
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

log = logging.getLogger("cvbot.fonts")

FONT_DIR = Path("data") / "fonts"
BUILTIN = "Helvetica"

# Pilihan di ⚙️ Pengaturan > 🔤 Font CV: (nama keluarga font, label)
CHOICES = [
    ("Carlito", "Calibri (Carlito)"),
    ("Arimo", "Arial (Arimo)"),
    ("Tinos", "Times New Roman (Tinos)"),
    ("Caladea", "Cambria (Caladea)"),
    ("Gelasio", "Georgia (Gelasio)"),
    ("EB Garamond", "Garamond"),
    ("Lato", "Lato"),
    ("Roboto", "Roboto"),
    ("Open Sans", "Open Sans"),
    ("Montserrat", "Montserrat"),
    ("Poppins", "Poppins"),
    (BUILTIN, "Helvetica (bawaan PDF)"),
]

# Font CV asli (huruf kecil, tanpa spasi) -> padanan di Google Fonts
SUBSTITUTES = {
    "calibri": "Carlito", "aptos": "Carlito", "arial": "Arimo", "liberationsans": "Arimo",
    "timesnewroman": "Tinos", "times": "Tinos", "liberationserif": "Tinos", "cambria": "Caladea",
    "georgia": "Gelasio", "garamond": "EB Garamond", "ebgaramond": "EB Garamond", "palatino": "EB Garamond",
    "bookantiqua": "EB Garamond", "segoeui": "Open Sans", "verdana": "Open Sans", "tahoma": "Open Sans",
    "centurygothic": "Montserrat", "helvetica": BUILTIN, "helveticaneue": BUILTIN,
}

_registered = {}  # keluarga font -> (nama regular, nama bold)


def label(family: str) -> str:
    return next((lbl for fam, lbl in CHOICES if fam == family), family)


def clean_name(raw: str) -> str:
    """'ABCDEF+OpenSans-Bold' / 'TimesNewRomanPSMT' / 'Calibri,Bold' -> 'Open Sans' / 'Times New Roman' / 'Calibri'"""
    name = raw.lstrip("/").split("+", 1)[-1]
    name = re.split(r"[-,]", name)[0]
    name = re.sub(r"(PSMT|PS|MT)$", "", name)
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)  # OpenSans -> Open Sans
    return " ".join(name.split())


def resolve(raw: str | None) -> str:
    """Nama font dari CV asli -> keluarga font yang dipakai di PDF."""
    if not raw:
        return "Carlito"  # font CV paling umum (Calibri) kalau font asli tidak diketahui
    name = clean_name(raw)
    key = name.lower().replace(" ", "")
    for prefix, family in SUBSTITUTES.items():
        if key.startswith(prefix):
            return family
    return name  # dicoba langsung di Google Fonts (Poppins, Montserrat, Lato, dll. dari template Canva)


def ensure(family: str) -> tuple[str, str, bool]:
    """Daftarkan font ke reportlab. Mengembalikan (font regular, font bold, berhasil?)."""
    if family == BUILTIN:
        return "Helvetica", "Helvetica-Bold", True
    if family in _registered:
        return (*_registered[family], True)
    try:
        regular, bold = _download(family)
    except Exception as e:
        log.warning("font %s tidak bisa dipakai, pakai Helvetica: %s", family, e)
        return "Helvetica", "Helvetica-Bold", False
    names = (f"{family}-Regular", f"{family}-Bold")
    pdfmetrics.registerFont(TTFont(names[0], str(regular)))
    pdfmetrics.registerFont(TTFont(names[1], str(bold)))
    addMapping(names[0], 0, 0, names[0])
    addMapping(names[0], 1, 0, names[1])
    _registered[family] = names
    return (*names, True)


def _download(family: str) -> tuple[Path, Path]:
    safe = re.sub(r"[^\w]+", "_", family)
    files = {w: FONT_DIR / f"{safe}-{w}.ttf" for w in ("400", "700")}
    if all(f.exists() for f in files.values()):
        return files["400"], files["700"]
    if files["400"].exists() and not files["700"].exists() and (FONT_DIR / f"{safe}-nobold").exists():
        return files["400"], files["400"]

    resp = requests.get("https://fonts.googleapis.com/css2", params={"family": f"{family}:wght@400;700"}, timeout=20)
    if resp.status_code == 400:  # font tanpa varian bold
        resp = requests.get("https://fonts.googleapis.com/css2", params={"family": family}, timeout=20)
    if resp.status_code == 400:
        raise ValueError(f"'{family}' tidak ada di Google Fonts")
    resp.raise_for_status()
    urls = dict(re.findall(r"font-weight:\s*(\d+);.*?src:\s*url\((\S+?\.ttf)\)", resp.text, re.S))
    if "400" not in urls:
        raise ValueError(f"file TTF untuk '{family}' tidak ditemukan")
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    for weight in ("400", "700"):
        if weight in urls:
            data = requests.get(urls[weight], timeout=30)
            data.raise_for_status()
            files[weight].write_bytes(data.content)
    if "700" not in urls:
        (FONT_DIR / f"{safe}-nobold").touch()
        return files["400"], files["400"]
    return files["400"], files["700"]


def supports(font_name: str, ch: str) -> bool:
    """Apakah font punya karakter ini (supaya tidak muncul kotak kosong di PDF)."""
    font = pdfmetrics.getFont(font_name)
    face = getattr(font, "face", None)
    if face is None or not hasattr(face, "charToGlyph"):  # font bawaan PDF: hanya Latin
        return ch in "\n\t" or _cp1252(ch)
    return ch.isspace() or ord(ch) in face.charToGlyph


def _cp1252(ch: str) -> bool:
    try:
        ch.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False
