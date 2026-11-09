"""Baca/tulis file JSON di data/ dengan aman.

Menulis lewat file sementara lalu os.replace, jadi file tidak pernah setengah tertulis atau tertimpa
sebagian (misalnya kalau dua proses bot sempat jalan bersamaan). File yang terlanjur rusak dipulihkan
dari objek JSON pertama yang utuh, dan salinan aslinya disimpan sebagai <nama>.rusak.
"""
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger("cvbot.storage")


def read_json(path: Path, default):
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        try:
            value, _ = json.JSONDecoder().raw_decode(text.lstrip())
        except json.JSONDecodeError:
            raise RuntimeError(f"File {path} rusak dan tidak bisa dibaca ({e}).") from e
        backup = path.with_name(path.name + ".rusak")
        backup.write_text(text, encoding="utf-8")
        log.warning("%s rusak (%s); dipulihkan, salinan asli di %s", path, e, backup)
        write_json(path, value, private=True)
        return value


def write_json(path: Path, data, private: bool = False) -> None:
    """private=True: hanya bisa dibaca pemilik file (untuk password / API key)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
