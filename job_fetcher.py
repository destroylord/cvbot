"""Membuka halaman lowongan dan mengambil teksnya."""
import json
import re

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}
MAX_CHARS = 15000


def fetch_job_text(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
    except requests.RequestException as e:
        raise RuntimeError(f"halaman tidak bisa dibuka ({type(e).__name__})") from e
    if resp.status_code in (401, 403, 429, 999):  # 999: kode blokir khas LinkedIn
        raise RuntimeError(f"situs memblokir bot (HTTP {resp.status_code})")
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}")
    if "html" not in resp.headers.get("Content-Type", "html"):
        raise RuntimeError("link bukan halaman web")

    soup = BeautifulSoup(resp.text, "html.parser")
    # Situs lowongan besar menaruh detail lowongan di data terstruktur JobPosting; paling bersih
    structured = _job_posting_text(soup)
    page = _visible_text(soup)
    text = f"{structured}\n\n{page}" if structured else page
    return text[:MAX_CHARS]


def _job_posting_text(soup: BeautifulSoup) -> str:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk(data):
            if item.get("@type") == "JobPosting":
                return _format_posting(item)
    return ""


def _walk(data):
    """Cari semua objek di dalam JSON-LD (bisa list, dict, atau @graph)."""
    if isinstance(data, list):
        for x in data:
            yield from _walk(x)
    elif isinstance(data, dict):
        yield data
        yield from _walk(data.get("@graph") or [])


def _format_posting(p: dict) -> str:
    org = p.get("hiringOrganization")
    org = org.get("name") if isinstance(org, dict) else org
    location = p.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else None
    if isinstance(location, dict):
        addr = location.get("address") or {}
        location = ", ".join(str(addr.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")
                             if isinstance(addr, dict) and addr.get(k))
    description = BeautifulSoup(str(p.get("description") or ""), "html.parser").get_text("\n")
    fields = [("Posisi", p.get("title")), ("Perusahaan", org), ("Lokasi", location),
              ("Jenis", p.get("employmentType")), ("Tenggat", p.get("validThrough"))]
    lines = [f"{k}: {v}" for k, v in fields if v]
    return "\n".join(lines) + "\n\n" + _tidy(description)


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return _tidy(main.get_text("\n"))


def _tidy(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
