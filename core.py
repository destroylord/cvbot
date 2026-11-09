"""Bagian bersama semua fitur: konfigurasi dari .env, log, data pengguna, alat bantu Telegram, dan
layanan yang dipakai banyak fitur (font CV, ubah profil, PDF CV asli).
"""
import asyncio
import logging
import os
import re
import sys
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from dotenv import load_dotenv

load_dotenv()

import cv_builder  # noqa: E402
import cv_fonts  # noqa: E402
import gmail_accounts  # noqa: E402
import profile_edit as pe  # noqa: E402
import storage  # noqa: E402
import tracker  # noqa: E402


DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
# Log ke file juga, karena saat jalan di background (pythonw) tidak ada jendela console
_log_handlers = [RotatingFileHandler(DATA_DIR / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
if sys.stderr:
    _log_handlers.append(logging.StreamHandler())
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO, handlers=_log_handlers)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("cvbot")
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()}
TAILOR_THRESHOLD = int(os.getenv("CV_TAILOR_THRESHOLD", "70"))
MIN_REC_SCORE = int(os.getenv("MIN_REC_SCORE", "60"))
MAX_RECS = int(os.getenv("MAX_RECS", "8"))
LOOKBACK_DAYS = int(os.getenv("ALERT_LOOKBACK_DAYS", "3"))
DAILY_CHECK_TIME = os.getenv("DAILY_CHECK_TIME", "08:00")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Jakarta")
FOLLOWUP_DAYS = int(os.getenv("FOLLOWUP_DAYS", "7"))
REPLY_CHECK_MINUTES = int(os.getenv("REPLY_CHECK_MINUTES", "60"))
MAX_SEND_PER_DAY = int(os.getenv("MAX_SEND_PER_DAY", "20"))  # per akun Gmail, supaya tidak dianggap spam
GAYA_LABEL = {"formal": "🎩 Formal", "santai": "😊 Santai", "singkat": "⚡ Singkat"}
JENIS_LABEL = {
    "interview": "🎤 Undangan interview / tes", "ditolak": "❌ Penolakan", "diterima": "🎉 Tawaran kerja",
    "info": "💬 Balasan / permintaan info", "otomatis": "🤖 Konfirmasi otomatis",
}
IMAGE_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Jenis balasan HR yang tetap berbunyi; notifikasi lain dikirim tanpa suara
LOUD_REPLIES = {"interview", "diterima", "info"}
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
URL_RE = re.compile(r"https?://\S+")


def load_user(uid: int) -> dict:
    data = storage.read_json(DATA_DIR / f"{uid}.json", {})
    tracker.migrate(data, TIMEZONE)
    return data


def save_user(uid: int, data: dict) -> None:
    storage.write_json(DATA_DIR / f"{uid}.json", data)


def update_user(uid: int, fn):
    """Baca-ubah-simpan dalam satu langkah (tanpa await di tengah) supaya data tidak saling timpa."""
    data = load_user(uid)
    result = fn(data)
    save_user(uid, data)
    return result


async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def finish_progress(progress, text: str | None = None):
    """Pesan "⏳ ..." diganti hasil akhirnya, atau dihapus kalau hasilnya dikirim sebagai pesan lain."""
    if progress is None:
        return
    try:
        if text:
            await progress.edit_text(text)
        else:
            await progress.delete()
    except BadRequest:
        pass


def user_dir(uid: int) -> Path:
    d = DATA_DIR / str(uid)
    d.mkdir(exist_ok=True)
    return d


def safe_name(text: str, limit: int = 40) -> str:
    return re.sub(r"[^\w\-]+", "_", text or "").strip("_")[:limit] or "file"


def only_allowed(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if ALLOWED and update.effective_user.id not in ALLOWED:
            if update.effective_message:
                await update.effective_message.reply_text("Maaf, bot ini privat.")
            return
        return await func(update, context)
    return wrapper


def kb(*rows):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in rows])


GMAIL_MISSING = "Belum ada akun Gmail. Tambahkan lewat ⚙️ Pengaturan > 📧 Akun Gmail."


APP_PASSWORD_RE = re.compile(r"[a-zA-Z]{16}")


def gmail_configured() -> bool:
    return bool(gmail_accounts.load())


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🎯 Rekomendasi", "📂 Lamaran"], ["📬 Cek Balasan", "📊 Statistik"], ["📄 CV Saya", "⚙️ Pengaturan"]],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Kirim link, teks, atau foto lowongan")


def default_account(data: dict) -> gmail_accounts.Account | None:
    accounts = gmail_accounts.load()
    return gmail_accounts.get(data.get("gmail_default")) or (accounts[0] if accounts else None)


def cv_font_for(data: dict) -> str:
    """Font PDF CV: pilihan pengguna, atau mengikuti font CV asli (default)."""
    choice = data.get("font_cv", "auto")
    return choice if choice != "auto" else cv_fonts.resolve(data.get("font_asli"))


def edit_profile(uid: int, fn):
    """Ubah profil CV asli, tandai sudah diubah supaya lampiran CV asli dibuat ulang."""
    def apply(d):
        result = fn(d["profile"])
        d["profile_edited"] = True
        d["cv_master_pdf"] = None
        return result
    return update_user(uid, apply)


def _guess_lang(profile: dict) -> str:
    words = " ".join([profile.get("ringkasan") or ""] + [p for e in pe.entries(dict(profile), "pengalaman")
                                                         for p in e["poin"]]).lower().split()
    english = sum(w in ("the", "and", "with", "for", "of", "to") for w in words)
    indonesian = sum(w in ("dan", "yang", "dengan", "untuk", "di", "ke") for w in words)
    return "en" if english > indonesian else "id"


async def ensure_master_cv(uid: int) -> str | None:
    """PDF CV asli terbaru. Kalau CV asli diubah lewat bot, PDF dibuat ulang dari datanya."""
    data = load_user(uid)
    if not data.get("profile_edited"):
        return data.get("cv_path")
    if data.get("cv_master_pdf") and Path(data["cv_master_pdf"]).exists():
        return data["cv_master_pdf"]
    profile = data["profile"]
    path = user_dir(uid) / f"CV_{safe_name(profile.get('nama') or 'Kandidat')}.pdf"
    await asyncio.to_thread(cv_builder.build_pdf, profile, str(path), _guess_lang(profile), cv_font_for(data))
    update_user(uid, lambda d: d.__setitem__("cv_master_pdf", str(path)))
    return str(path)
