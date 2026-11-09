"""Pengaturan yang bisa diubah dari bot (⚙️ Pengaturan), disimpan di data/settings.json.

Nilai dari bot diutamakan. Kalau belum pernah diatur dari bot, pakai nilai di .env, lalu nilai default.
Berisi API key, jadi file-nya hanya bisa dibaca pemiliknya.
"""
import os
from pathlib import Path

import storage

STORE = Path("data") / "settings.json"


def _read() -> dict:
    return storage.read_json(STORE, {})


def get(name: str, default: str = "") -> str:
    stored = _read()
    if name in stored:  # termasuk "" (misalnya model cadangan sengaja dikosongkan)
        return stored[name]
    return os.getenv(name, default)


def set(name: str, value: str) -> None:
    data = _read()
    data[name] = value
    storage.write_json(STORE, data, private=True)


def masked(value: str) -> str:
    """API key untuk ditampilkan: hanya 4 karakter terakhir."""
    return f"…{value[-4:]}" if value else "belum ada"
