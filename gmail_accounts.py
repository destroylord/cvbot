"""Daftar akun Gmail.

Sumbernya dua:
  - .env: GMAIL_ADDRESS / GMAIL_APP_PASSWORD, lalu GMAIL_ADDRESS_2 / GMAIL_APP_PASSWORD_2, dst. (hanya dibaca)
  - data/gmail_accounts.json: akun yang ditambahkan lewat bot (⚙️ Pengaturan > 📧 Akun Gmail),
    bisa ditambah, diganti app password-nya, dan dihapus dari Telegram.
"""
import imaplib
import json
import os
import smtplib
from dataclasses import dataclass, field
from pathlib import Path

MAX_ENV_ACCOUNTS = 20
STORE = Path("data") / "gmail_accounts.json"


@dataclass(frozen=True)
class Account:
    address: str
    password: str
    from_env: bool = field(default=False, compare=False)


def _env_accounts() -> list[Account]:
    accounts = []
    for i in range(1, MAX_ENV_ACCOUNTS + 1):
        suffix = "" if i == 1 else f"_{i}"
        address = os.getenv(f"GMAIL_ADDRESS{suffix}", "").strip()
        password = os.getenv(f"GMAIL_APP_PASSWORD{suffix}", "").replace(" ", "")
        if address and password:
            accounts.append(Account(address, password, from_env=True))
    return accounts


def _read_store() -> list[dict]:
    if not STORE.exists():
        return []
    return json.loads(STORE.read_text(encoding="utf-8"))


def _write_store(items: list[dict]) -> None:
    STORE.parent.mkdir(exist_ok=True)
    STORE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(STORE, 0o600)  # berisi app password: hanya bisa dibaca pemilik file
    except OSError:
        pass


def load() -> list[Account]:
    """Akun dari .env dulu, lalu akun dari bot. Alamat yang sama hanya dihitung sekali."""
    accounts, seen = [], set()
    stored = [Account(i["address"], i["password"]) for i in _read_store()]
    for account in _env_accounts() + stored:
        if account.address.lower() not in seen:
            seen.add(account.address.lower())
            accounts.append(account)
    return accounts


def get(address: str | None) -> Account | None:
    """Akun dengan alamat ini. Tanpa alamat (data lama), pakai akun utama."""
    accounts = load()
    if not address:
        return accounts[0] if accounts else None
    return next((a for a in accounts if a.address.lower() == address.lower()), None)


def own_addresses() -> set[str]:
    return {a.address.lower() for a in load()}


def verify(address: str, password: str) -> None:
    """Coba login IMAP (baca email) dan SMTP (kirim email). Melempar error kalau gagal."""
    with imaplib.IMAP4_SSL("imap.gmail.com", timeout=20) as imap:
        imap.login(address, password)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
        smtp.login(address, password)


def save(address: str, password: str) -> None:
    """Tambah akun baru, atau ganti app password akun yang sudah ada di data bot."""
    if any(a.address.lower() == address.lower() for a in _env_accounts()):
        raise ValueError(f"{address} diatur di file .env, ubah lewat .env.")
    items = _read_store()
    existing = next((i for i in items if i["address"].lower() == address.lower()), None)
    if existing:
        existing["password"] = password
    else:
        items.append({"address": address, "password": password})
    _write_store(items)


def remove(address: str) -> None:
    if any(a.address.lower() == address.lower() for a in _env_accounts()):
        raise ValueError(f"{address} diatur di file .env, hapus lewat .env.")
    _write_store([i for i in _read_store() if i["address"].lower() != address.lower()])
