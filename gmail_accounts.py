"""Daftar akun Gmail dari .env: GMAIL_ADDRESS / GMAIL_APP_PASSWORD untuk akun utama,
lalu GMAIL_ADDRESS_2 / GMAIL_APP_PASSWORD_2, GMAIL_ADDRESS_3 / ..., dst. untuk akun tambahan."""
import os
from dataclasses import dataclass

MAX_ACCOUNTS = 20


@dataclass(frozen=True)
class Account:
    address: str
    password: str


def load() -> list[Account]:
    accounts = []
    for i in range(1, MAX_ACCOUNTS + 1):
        suffix = "" if i == 1 else f"_{i}"
        address = os.getenv(f"GMAIL_ADDRESS{suffix}", "").strip()
        password = os.getenv(f"GMAIL_APP_PASSWORD{suffix}", "").replace(" ", "")
        if address and password and address.lower() not in {a.address.lower() for a in accounts}:
            accounts.append(Account(address, password))
    return accounts


def get(address: str | None) -> Account | None:
    """Akun dengan alamat ini. Tanpa alamat (data lama), pakai akun utama."""
    accounts = load()
    if not address:
        return accounts[0] if accounts else None
    return next((a for a in accounts if a.address.lower() == address.lower()), None)


def own_addresses() -> set[str]:
    return {a.address.lower() for a in load()}
