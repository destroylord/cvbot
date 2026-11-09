"""💾 Backup data ke Telegram dan pulihkan dari file zip.
"""
import io
import json
import re
import zipfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

import tracker
from core import (
    DATA_DIR, TIMEZONE, kb, main_menu, only_allowed, save_user,
    user_dir,
)
import fitur_kirim


# ---- 💾 Backup & pulihkan
def build_backup(uid: int) -> bytes:
    """Zip berisi data pengguna (profil, lamaran, pengaturan pribadi) dan file CV. Tanpa password/API key."""
    buf = io.BytesIO()
    folder = DATA_DIR / str(uid)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{uid}.json", (DATA_DIR / f"{uid}.json").read_text(encoding="utf-8")
                   if (DATA_DIR / f"{uid}.json").exists() else "{}")
        if folder.exists():
            for f in folder.rglob("*"):
                if f.is_file() and not f.name.endswith(".zip"):
                    z.write(f, f"{uid}/{f.relative_to(folder).as_posix()}")
        z.writestr("BACA.txt", "Backup CV Bot. Kirim file zip ini ke bot untuk memulihkan.\n"
                               "Tidak berisi app password Gmail maupun API key AI.\n")
    return buf.getvalue()


async def send_backup(message, uid: int):
    data = build_backup(uid)
    name = f"cvbot-backup-{tracker.now(TIMEZONE).strftime('%Y%m%d-%H%M')}.zip"
    await message.reply_document(io.BytesIO(data), filename=name, caption=(
        "💾 Backup data kamu: profil CV, lamaran, jawaban PAR, preferensi, dan file CV. Tidak berisi app password "
        "Gmail maupun API key. Simpan baik-baik, isinya data pribadi.\n\nUntuk memulihkan, kirim file ini ke bot."))


@only_allowed
async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_backup(update.message, update.effective_user.id)


def _safe_member(name: str) -> bool:
    parts = Path(name).parts
    return not name.startswith(("/", "\\")) and ".." not in parts and ":" not in name


async def receive_backup(message, context, uid: int, doc):
    tmp = user_dir(uid) / "pulihkan.zip"
    file = await doc.get_file()
    await file.download_to_drive(tmp)
    try:
        with zipfile.ZipFile(tmp) as z:
            jsons = [n for n in z.namelist() if re.fullmatch(r"\d+\.json", n)]
            if len(jsons) != 1 or not all(_safe_member(n) for n in z.namelist()):
                raise ValueError("bukan file backup CV Bot")
            content = json.loads(z.read(jsons[0]).decode("utf-8"))
            files = [n for n in z.namelist() if n.startswith(jsons[0][:-5] + "/") and not n.endswith("/")]
    except (zipfile.BadZipFile, ValueError, UnicodeDecodeError) as e:
        tmp.unlink(missing_ok=True)
        await message.reply_text(f"File ini tidak bisa dipulihkan: {e}.")
        return
    context.user_data["restore"] = {"path": str(tmp), "old": jsons[0][:-5]}
    name = (content.get("profile") or {}).get("nama") or "-"
    await message.reply_text(
        f"♻️ Backup berisi: CV atas nama {name}, {len(content.get('lamaran') or [])} lamaran, {len(files)} file.\n\n"
        "Pulihkan? Data kamu sekarang akan diganti (salinannya disimpan dulu sebagai cadangan).",
        reply_markup=kb([("♻️ Pulihkan", "bk:restore"), ("❌ Batal", "bk:cancel")]))


def _rewrite_paths(value, old: str, new: str):
    """Ganti folder data lama (data/<uid lama>/) dengan folder pengguna sekarang di semua path tersimpan."""
    if isinstance(value, dict):
        return {k: _rewrite_paths(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite_paths(v, old, new) for v in value]
    if isinstance(value, str):
        for sep in ("/", "\\"):
            prefix = f"data{sep}{old}{sep}"
            if value.startswith(prefix):
                return str(DATA_DIR / new / value[len(prefix):].replace("\\", "/"))
    return value


async def btn_backup(query, context, uid, data, arg):
    """bk (kirim backup), bk:restore, bk:cancel"""
    if not arg:
        await send_backup(query.message, uid)
        return
    restore = context.user_data.pop("restore", None)
    await query.edit_message_reply_markup(None)
    if not restore or not Path(restore["path"]).exists():
        await query.message.reply_text("File backup sudah tidak ada. Kirim ulang file zip-nya.")
        return
    if arg == "cancel":
        Path(restore["path"]).unlink(missing_ok=True)
        await query.message.reply_text("Dibatalkan, data tidak diubah.")
        return
    safety = user_dir(uid) / f"sebelum-pulih-{tracker.now(TIMEZONE).strftime('%Y%m%d-%H%M%S')}.zip"
    safety.write_bytes(build_backup(uid))
    old, folder = restore["old"], user_dir(uid)
    with zipfile.ZipFile(restore["path"]) as z:
        content = json.loads(z.read(f"{old}.json").decode("utf-8"))
        for name in z.namelist():
            if name.startswith(f"{old}/") and not name.endswith("/") and _safe_member(name):
                target = folder / name[len(old) + 1:]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
    Path(restore["path"]).unlink(missing_ok=True)
    content = _rewrite_paths(content, old, str(uid))
    save_user(uid, content)
    for item in content.get("antrian") or []:
        fitur_kirim.schedule_queued(context.job_queue, uid, item)
    await query.message.reply_text(f"✅ Data dipulihkan. Data sebelumnya disimpan di {safety.name}.",
                                   reply_markup=main_menu())
