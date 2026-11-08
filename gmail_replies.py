"""Mencari email balasan HR di Gmail untuk lamaran yang sudah dikirim."""
import email
import imaplib
import os
from email.utils import parseaddr

from job_alerts import _decode, _message_to_text

# Domain email umum: untuk ini hanya alamat persis yang dicocokkan, bukan seluruh domain
FREE_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.id", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "icloud.com", "proton.me", "protonmail.com", "aol.com",
}


def _pick_app(candidates: list[dict], subject: str, text: str) -> dict:
    """Kalau ada beberapa lamaran ke perusahaan yang sama, pilih yang posisinya disebut di email."""
    haystack = f"{subject} {text}".lower()
    for app in sorted(candidates, key=lambda a: a["id"], reverse=True):
        posisi = (app.get("posisi") or "").lower()
        if posisi and posisi in haystack:
            return app
    return max(candidates, key=lambda a: a["id"])


def find_replies(apps: list[dict], seen_ids: set, sent_dates: dict, per_query: int = 10) -> list[dict]:
    user = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")

    queries = {}  # kriteria FROM -> daftar lamaran
    for app in apps:
        to = (app.get("ke") or "").strip().lower()
        if "@" not in to:
            continue
        queries.setdefault(to, []).append(app)
        domain = to.split("@", 1)[1]
        if domain not in FREE_DOMAINS:
            queries.setdefault("@" + domain, []).append(app)

    results, processed = [], set()
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        for criterion, candidates in queries.items():
            since = min(sent_dates[a["id"]] for a in candidates).strftime("%d-%b-%Y")
            status, data = imap.search(None, "SINCE", since, "FROM", f'"{criterion}"')
            if status != "OK" or not data or not data[0]:
                continue
            for num in sorted(data[0].split(), key=int, reverse=True)[:per_query]:
                if num in processed:
                    continue
                processed.add(num)
                status, fetched = imap.fetch(num, "(RFC822)")
                if status != "OK" or not fetched or not fetched[0]:
                    continue
                msg = email.message_from_bytes(fetched[0][1])
                msg_id = (msg.get("Message-ID") or num.decode()).strip()
                if msg_id in seen_ids:
                    continue
                if parseaddr(msg.get("From"))[1].lower() == user.lower():
                    continue
                subject = _decode(msg.get("Subject"))
                text, _ = _message_to_text(msg)
                app = _pick_app(candidates, subject, text)
                results.append({"app_id": app["id"], "id": msg_id, "from": _decode(msg.get("From")),
                                "subject": subject, "text": text[:6000]})
    return results
