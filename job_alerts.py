"""Membaca email job alert (JobStreet, Glints, LinkedIn, dll.) dari Gmail lewat IMAP.

Hanya membaca: mailbox dibuka read-only, jadi email tidak ditandai sudah dibaca.
Link di email diganti penanda [L1], [L2], ... supaya AI bisa menyebut link mana milik lowongan mana,
lalu penanda itu dipetakan kembali ke URL aslinya.
"""
import datetime as dt
import email
import imaplib
import os
import re
from email.header import decode_header, make_header
from email.message import Message

from bs4 import BeautifulSoup

from gmail_accounts import Account

DEFAULT_SENDERS = "jobstreet,glints,linkedin,kalibrr,indeed"
PER_SENDER = 10  # email terbaru per pengirim yang diambil sekali cek, supaya kuota AI tidak jebol
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# Link non-lowongan yang dibuang. Sengaja sempit: judul lowongan bisa saja berisi "Support", "Help Desk", dll.
SKIP_LINK_RE = re.compile(r"unsubscribe|berhenti berlangganan|email preferences|kebijakan privasi|privacy policy",
                          re.I)


def alert_senders() -> list[str]:
    return [s.strip() for s in os.getenv("ALERT_SENDERS", DEFAULT_SENDERS).split(",") if s.strip()]


def fetch_alert_emails(seen: set, days: int, account: Account) -> list[dict]:
    """Email job alert baru dari satu akun.
    Mengembalikan list {"id", "from", "subject", "text", "links": {"L1": url, ...}}."""
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
    results, done = [], set()
    with imaplib.IMAP4_SSL("imap.gmail.com", timeout=30) as imap:
        imap.login(account.address, account.password)
        imap.select("INBOX", readonly=True)
        for sender in alert_senders():
            status, data = imap.search(None, "SINCE", since, "FROM", f'"{sender}"')
            if status != "OK" or not data or not data[0]:
                continue
            for num in sorted(data[0].split(), key=int, reverse=True)[:PER_SENDER]:
                if num in done:
                    continue
                done.add(num)
                status, fetched = imap.fetch(num, "(BODY.PEEK[])")
                if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                msg = email.message_from_bytes(fetched[0][1])
                msg_id = (msg.get("Message-ID") or f"{account.address}:{num.decode()}").strip()
                if msg_id in seen:
                    continue
                text, links = _message_to_text(msg)
                if not text.strip():
                    continue
                results.append({"id": msg_id, "from": _decode(msg.get("From")), "subject": _decode(msg.get("Subject")),
                                "text": text, "links": links})
    return results


def _decode(value) -> str:
    """Header email (Subject, From) bisa ter-encode, misalnya =?UTF-8?B?...?=."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def _message_to_text(msg: Message) -> tuple[str, dict]:
    """Isi email sebagai teks + peta penanda link. Bagian HTML diutamakan karena link-nya lengkap."""
    html, plain = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype == "text/html" and html is None:
            html = _payload(part)
        elif ctype == "text/plain" and plain is None:
            plain = _payload(part)
    links = _Links()
    if html:
        return _html_to_text(html, links), links.refs
    return _plain_to_text(plain or "", links), links.refs


def _payload(part: Message) -> str:
    raw = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class _Links:
    """Memberi penanda [L1], [L2], ... untuk tiap URL unik."""

    def __init__(self):
        self.refs = {}
        self._by_url = {}

    def ref(self, url: str) -> str:
        if url not in self._by_url:
            key = f"L{len(self.refs) + 1}"
            self._by_url[url] = key
            self.refs[key] = url
        return f"[{self._by_url[url]}]"


def _html_to_text(html: str, links: _Links) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head", "img"]):
        tag.decompose()
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        label = a.get_text(" ", strip=True)
        if href.startswith("http") and label and not SKIP_LINK_RE.search(label + " " + href[:80]):
            a.replace_with(f"{label} {links.ref(href)}")
        else:
            a.replace_with(label)
    return _tidy(soup.get_text("\n"))


def _plain_to_text(text: str, links: _Links) -> str:
    def replace(m):
        # Tanda baca di akhir kalimat bukan bagian dari URL
        url = m.group(0).rstrip(".,;:!?")
        return links.ref(url) + m.group(0)[len(url):]
    return _tidy(URL_RE.sub(replace, text))


def _tidy(text: str) -> str:
    lines = [re.sub(r"[ \t ‌​]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
