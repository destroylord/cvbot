"""Bot Telegram: scan CV, rekomendasi lowongan dari Gmail job alert, CV tailoring,
email lamaran, dan kirim lamaran via Gmail (semi-otomatis: selalu minta konfirmasi)."""
import asyncio
import datetime as dt
import hashlib
import io
import json
import logging
import os
import difflib
import re
import sys
import time
import zipfile
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from telegram import (  # noqa: E402
    BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update,
)
from telegram.error import BadRequest  # noqa: E402
from telegram.ext import (  # noqa: E402
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, PicklePersistence, filters,
)

import ai_helper  # noqa: E402
import app_settings  # noqa: E402
import cv_builder  # noqa: E402
import cv_fonts  # noqa: E402
import cv_parser  # noqa: E402
import gmail_accounts  # noqa: E402
import gmail_replies  # noqa: E402
import job_alerts  # noqa: E402
import job_fetcher  # noqa: E402
import job_rules as rules  # noqa: E402
import mailer  # noqa: E402
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

COMMANDS = [
    ("rekomendasi", "Cek lowongan dari email job alert"),
    ("lamaran", "Daftar lamaran, ubah status, follow-up"),
    ("cekbalasan", "Cek balasan HR di Gmail sekarang"),
    ("statistik", "Statistik lamaran"),
    ("cv", "Lihat & ubah CV asli kamu"),
    ("pengaturan", "Model AI, gaya email, font CV, akun Gmail, notifikasi"),
    ("revisi", "Revisi email lamaran / follow-up terakhir"),
    ("backup", "Backup data CV & lamaran ke Telegram"),
    ("start", "Cara pakai & tampilkan menu"),
]

# Jenis balasan HR yang tetap berbunyi; notifikasi lain dikirim tanpa suara
LOUD_REPLIES = {"interview", "diterima", "info"}

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
URL_RE = re.compile(r"https?://\S+")


# ================================================================ util
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


def job_summary(job: dict) -> str:
    score = int(job.get("skor") or 0)
    bar = "🟩" * (score // 20) + "⬜" * (5 - score // 20)
    return (f"📋 {job.get('posisi')} - {job.get('perusahaan')}\n"
            f"Skor kecocokan: {score}/100 {bar}\n\n"
            f"✅ Cocok: {', '.join(job.get('skill_cocok') or []) or '-'}\n"
            f"⚠️ Kurang: {', '.join(job.get('skill_kurang') or []) or '-'}\n\n"
            f"{job.get('alasan') or ''}\n"
            f"Email HR: {job.get('email_hr') or 'tidak ditemukan'}"
            + "".join(f"\n\n{w}" for w in job.get("peringatan") or []))


def job_warnings(data: dict, job: dict, job_text: str) -> list[str]:
    """Peringatan untuk satu lowongan: tanda penipuan, lamaran dobel, tidak sesuai preferensi."""
    warnings = []
    signals, serious = rules.scam_signals(job_text, job)
    job["penipuan_serius"] = serious
    if signals:
        head = "🚨 Waspada kemungkinan penipuan:" if serious else "⚠️ Perlu dicek dulu:"
        tail = "\nPerusahaan resmi tidak pernah meminta uang atau data sensitif dari pelamar." if serious else ""
        warnings.append(head + "".join(f"\n• {x}" for x in signals) + tail)
    dups = rules.find_duplicates(data.get("lamaran") or [], job.get("posisi"), job.get("perusahaan"))
    if dups:
        lines = [f"\n• #{a['id']} {a.get('posisi')} ({tracker.days_since(a, TIMEZONE)} hari lalu, "
                 f"{tracker.STATUS.get(a.get('status'), a.get('status'))})" for a in dups[-3:]]
        warnings.append(f"🔁 Kamu sudah pernah melamar ke {job.get('perusahaan')}:" + "".join(lines))
    issues = rules.pref_issues(job, data.get("pref"))
    if issues:
        warnings.append("🎯 Tidak sesuai preferensi kamu:" + "".join(f"\n• {x}" for x in issues))
    return warnings


# ================================================================ commands
@only_allowed
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Cara pakainya:\n\n"
        "1. Kirim CV kamu (PDF/DOCX)\n"
        "2. Kirim link / teks / foto poster lowongan (bisa di-forward dari grup lain), "
        "atau tekan 🎯 Rekomendasi untuk cari dari email job alert\n"
        f"3. Kalau skornya di bawah {TAILOR_THRESHOLD}, CV otomatis disesuaikan (tetap jujur)\n"
        "4. Buat email lamaran, review, lalu kirim via Gmail dengan satu klik\n\n"
        "Pakai tombol menu di bawah, atau tombol Menu di samping kolom chat untuk daftar perintah. "
        "Revisi email lamaran: /revisi <instruksi>\n\n"
        f"Otomatis: rekomendasi & pengingat follow-up tiap hari jam {DAILY_CHECK_TIME}, "
        f"cek balasan HR tiap {REPLY_CHECK_MINUTES} menit (bisa dimatikan di ⚙️ Pengaturan).",
        reply_markup=main_menu())


@only_allowed
async def profil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_user(update.effective_user.id)
    if not data.get("profile"):
        await update.message.reply_text("Belum ada CV. Kirim dulu file PDF/DOCX kamu.")
        return
    text, markup = cv_overview(data)
    await update.message.reply_text(text, reply_markup=markup)


# ================================================================ tracker lamaran
def lamaran_list_view(data: dict):
    apps = data.get("lamaran") or []
    queue = data.get("antrian") or []
    queue_row = [[(f"🕗 Antrean kirim ({len(queue)})", "q:list")]] if queue else []
    if not apps:
        return ("Belum ada lamaran yang terkirim lewat bot." + (f"\n🕗 {len(queue)} lamaran terjadwal." if queue else ""),
                kb(*queue_row) if queue else None)
    recent = apps[-10:][::-1]
    lines = [f"#{a['id']} {tracker.STATUS.get(a['status'], a['status'])} · {a.get('posisi')} - "
             f"{a.get('perusahaan')} ({tracker.days_since(a, TIMEZONE)} hari)" for a in recent]
    buttons = [(f"#{a['id']} {(a.get('perusahaan') or '-')[:18]}", f"app:{a['id']}") for a in recent]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    text = (f"📂 Lamaran kamu ({len(apps)} total, 10 terbaru):\n\n" + "\n".join(lines) +
            (f"\n\n🕗 {len(queue)} lamaran terjadwal belum terkirim." if queue else "") +
            "\n\nPilih lamaran untuk lihat detail, ubah status, atau buat follow-up.")
    return text, kb(*rows, *queue_row)


def app_detail_view(app: dict):
    days = tracker.days_since(app, TIMEZONE)
    text = (f"#{app['id']} {app.get('posisi')}\n"
            f"🏢 {app.get('perusahaan')}\n"
            f"📧 {app.get('ke')}\n"
            + (f"📤 Dari {app['dari']}\n" if app.get("dari") else "") +
            f"📅 Dikirim {tracker.sent_at(app, TIMEZONE).strftime('%d-%m-%Y %H:%M')} ({days} hari lalu)\n"
            f"Skor: {app.get('skor')} · {'CV disesuaikan' if app.get('cv_disesuaikan') else 'CV asli'}\n"
            f"Follow-up terkirim: {len(app.get('followups') or [])}x\n\n"
            f"Status: {tracker.STATUS.get(app['status'], app['status'])}")
    if app.get("balasan"):
        last = app["balasan"][-1]
        text += f"\n\nBalasan terakhir ({last.get('tanggal', '')[:10]}): {last.get('ringkasan')}"
    i = app["id"]
    return text, kb(
        [("💬 Dibalas", f"st:{i}:dibalas"), ("🎤 Interview", f"st:{i}:interview")],
        [("❌ Ditolak", f"st:{i}:ditolak"), ("🎉 Diterima", f"st:{i}:diterima")],
        [("👻 Tidak ada kabar", f"st:{i}:tidak_ada_kabar")],
        [("📨 Draft Follow-up", f"fu:{i}"), ("🎤 Persiapan interview", f"iv:{i}")],
        [("⬅️ Daftar lamaran", "lamlist")])


@only_allowed
async def lamaran(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = lamaran_list_view(load_user(update.effective_user.id))
    await update.message.reply_text(text, reply_markup=markup)


@only_allowed
async def statistik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(tracker.stats_text(load_user(update.effective_user.id), TIMEZONE))


def gaya_view(data: dict):
    current = data.get("gaya", "formal")
    return (f"Gaya email lamaran sekarang: {GAYA_LABEL.get(current)}\n\n"
            "Isi email tidak mengulang isi CV (CV sudah dilampirkan).\n\n"
            "🎩 Formal: sopan dan profesional, 120-180 kata\n"
            "😊 Santai: hangat tapi tetap sopan, 90-140 kata\n"
            "⚡ Singkat: langsung ke inti, 50-80 kata",
            kb([(label, f"gaya:{key}") for key, label in GAYA_LABEL.items()]))


@only_allowed
async def gaya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = gaya_view(load_user(update.effective_user.id))
    await update.message.reply_text(text, reply_markup=markup)


TOGGLES = {"auto_rekomendasi": "🎯 Rekomendasi harian", "auto_balasan": "📬 Cek balasan otomatis",
           "jadwal_kirim": "🕗 Kirim di jam kerja"}


def settings_view(data: dict):
    accounts = gmail_accounts.load()
    default = default_account(data)
    onoff = {key: "Aktif" if data.get(key, True) else "Mati" for key in TOGGLES}
    ai = ai_helper.PROVIDERS.get(ai_helper.provider(), {})
    text = (f"⚙️ Pengaturan\n\n"
            f"AI: {ai.get('label', ai_helper.provider()).split(' (')[0]} · {ai_helper.current_model() or '-'}\n"
            f"Gaya email lamaran: {GAYA_LABEL.get(data.get('gaya', 'formal'))}\n"
            f"Font CV: {cv_fonts.label(cv_font_for(data))}"
            f"{' (sama seperti CV asli)' if data.get('font_cv', 'auto') == 'auto' else ''}\n"
            f"Akun Gmail default: {default.address if default else 'belum ada'}"
            f"{f' ({len(accounts)} akun terhubung)' if len(accounts) > 1 else ''}\n"
            f"Rekomendasi harian jam {DAILY_CHECK_TIME}: {onoff['auto_rekomendasi']}\n"
            f"Cek balasan HR tiap {REPLY_CHECK_MINUTES} menit: {onoff['auto_balasan']}\n"
            f"Kirim lamaran hanya di jam kerja (Senin-Jumat {rules.WORK_START:02d}.00-{rules.WORK_END:02d}.00): "
            f"{onoff['jadwal_kirim']}\n"
            f"Batas kirim: {MAX_SEND_PER_DAY} lamaran/hari per akun Gmail\n\n"
            f"Jam cek, ambang skor, dan batas kirim diatur di file .env.")
    rows = [[("🤖 Model AI", "ai")], [("✍️ Gaya email lamaran", "set:gaya"), ("🔤 Font CV", "font")],
            [("📧 Akun Gmail", "acc"), ("🎯 Preferensi loker", "pref")], [("💾 Backup data", "bk")]]
    rows += [[(f"{label}: {'ON ✅' if data.get(key, True) else 'OFF ⛔'}", f"set:tog:{key}")]
             for key, label in TOGGLES.items()]
    return text, kb(*rows)


@only_allowed
async def pengaturan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = settings_view(load_user(update.effective_user.id))
    await update.message.reply_text(text, reply_markup=markup)


@only_allowed
async def cekbalasan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_replies(context, update.effective_user.id, update.effective_chat.id, manual=True)


@only_allowed
async def revisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instruction = " ".join(context.args)
    kind = context.user_data.get("draft_kind")
    followup = context.user_data.get("followup")
    if kind == "fu" and followup:
        current, label = followup["text"], "email follow-up"
    elif context.user_data.get("cover_letter"):
        kind, current, label = "cl", context.user_data["cover_letter"], "email lamaran"
    else:
        await update.message.reply_text("Belum ada email lamaran atau follow-up untuk direvisi.")
        return
    if not instruction:
        await update.message.reply_text("Tulis instruksinya, contoh: /revisi lebih singkat")
        return
    msg = await update.message.reply_text(f"⏳ Merevisi {label}...")
    try:
        new_text = await asyncio.to_thread(ai_helper.revise_text, current, instruction, label)
    except Exception as e:
        log.exception("revisi gagal")
        await msg.edit_text(f"Gagal merevisi: {e}")
        return
    if kind == "fu":
        followup["text"] = new_text
        await msg.edit_text(**followup_view(load_user(update.effective_user.id), followup))
    else:
        context.user_data["cover_letter"] = new_text
        await msg.edit_text(new_text, reply_markup=cover_letter_buttons())


@only_allowed
async def rekomendasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_recommendations(context, update.effective_user.id, update.effective_chat.id)


# ================================================================ rekomendasi
async def fetch_new_alert_emails(accounts, seen: set) -> tuple[list[dict], list[str]]:
    """Baca job alert baru dari semua akun. Mengembalikan (email, daftar pesan error per akun)."""
    emails, failed = [], []
    for account in accounts:
        try:
            found = await asyncio.to_thread(job_alerts.fetch_alert_emails, seen, LOOKBACK_DAYS, account)
        except Exception as e:
            log.exception("gagal membaca Gmail %s", account.address)
            failed.append(f"{account.address}: {e}")
            continue
        # Job alert yang sama bisa masuk ke beberapa akun
        known = {em["id"] for em in emails}
        emails += [em for em in found if em["id"] not in known]
    return emails, failed


async def jobs_from_emails(profile: dict, emails: list[dict]) -> tuple[list[dict], list[str]]:
    """Ambil & beri skor lowongan dari tiap email. Mengembalikan (lowongan, id email yang berhasil diproses)."""
    jobs, processed = [], []
    for em in emails:
        try:
            found = await asyncio.to_thread(ai_helper.extract_and_rank, profile, em["text"], em["from"])
        except Exception:
            log.exception("gagal memproses email %s", em["subject"])
            continue  # tidak ditandai 'seen' supaya dicoba lagi nanti
        for job in found:
            ref = job.get("link_ref")
            job["link"] = em["links"].get(ref) if ref else None
            job["sumber"] = em["from"]
            jobs.append(job)
        processed.append(em["id"])
    return jobs, processed


def dedupe_jobs(jobs: list[dict]) -> list[dict]:
    """Lowongan yang sama (posisi + perusahaan) dari beberapa email: simpan yang skornya tertinggi."""
    unique = {}
    for job in jobs:
        key = (str(job.get("posisi")).lower(), str(job.get("perusahaan")).lower())
        if key not in unique or (job.get("skor") or 0) > (unique[key].get("skor") or 0):
            unique[key] = job
    return list(unique.values())


def top_jobs(jobs: list[dict]) -> list[dict]:
    return sorted((j for j in jobs if (j.get("skor") or 0) >= MIN_REC_SCORE),
                  key=lambda j: j.get("skor") or 0, reverse=True)[:MAX_RECS]


def rec_id(job: dict) -> str:
    return hashlib.md5(f"{job.get('posisi')}|{job.get('perusahaan')}|{job.get('link')}".encode()).hexdigest()[:10]


def recommendations_message(top: list[dict]) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Semua rekomendasi dalam satu pesan, satu baris tombol per lowongan."""
    lines = [f"🎯 {len(top)} lowongan yang cocok dengan CV kamu:"]
    rows = []
    for i, job in enumerate(top, 1):
        lines.append(f"\n{i}. {job.get('skor')}/100 · {job.get('posisi')}\n"
                     f"🏢 {job.get('perusahaan')}" + (f" · {job.get('lokasi')}" if job.get("lokasi") else "") +
                     f"\n💬 {(job.get('alasan') or '')[:160]}")
        row = [InlineKeyboardButton(f"🔍 {i}. {str(job.get('posisi') or '')[:24]}",
                                    callback_data=f"rec:{rec_id(job)}")]
        link = job.get("link") or ""
        if link.startswith("http"):
            row.append(InlineKeyboardButton("🌐 Buka", url=link))
        rows.append(row)
    return "\n".join(lines)[:4000], rows


async def send_recommendations(context, uid: int, chat_id: int, silent_if_empty: bool = False):
    bot = context.bot
    data = load_user(uid)

    async def say(text):
        if not silent_if_empty:
            await bot.send_message(chat_id, text)

    if not data.get("profile"):
        await say("Kirim CV kamu dulu sebelum minta rekomendasi.")
        return
    if not gmail_configured():
        await say(GMAIL_MISSING)
        return

    accounts = gmail_accounts.load()
    progress = None
    if not silent_if_empty:
        progress = await bot.send_message(
            chat_id, f"⏳ Mengecek email job alert di {len(accounts)} akun Gmail..."
            if len(accounts) > 1 else "⏳ Mengecek email job alert...")
    seen = list(data.get("gmail_seen") or [])
    emails, failed = await fetch_new_alert_emails(accounts, set(seen))
    if failed:
        await say("Gagal membaca Gmail:\n" + "\n".join(failed))
        if len(failed) == len(accounts):
            await finish_progress(progress)
            return

    jobs, processed = await jobs_from_emails(data["profile"], emails)
    update_user(uid, lambda d: d.__setitem__("gmail_seen", (seen + processed)[-500:]))

    unique = dedupe_jobs(jobs)
    apps = data.get("lamaran") or []
    suitable = [j for j in unique if not rules.pref_issues(j, data.get("pref"))
                and not rules.find_duplicates(apps, j.get("posisi"), j.get("perusahaan"))]
    filtered = len(unique) - len(suitable)
    top = top_jobs(suitable)
    if not top:
        await finish_progress(progress, f"Tidak ada rekomendasi baru dengan skor ≥ {MIN_REC_SCORE} "
                                        f"({len(emails)} email baru dicek, {len(unique)} lowongan ditemukan"
                                        f"{f', {filtered} disaring' if filtered else ''}).")
        return
    await finish_progress(progress)

    recs = context.user_data.setdefault("recs", {})
    recs.update({rec_id(job): job for job in top})
    while len(recs) > 50:
        recs.pop(next(iter(recs)))
    text, rows = recommendations_message(top)
    if filtered:
        text = (text + f"\n\n🎯 {filtered} lowongan lain disaring: tidak sesuai preferensi atau sudah pernah "
                       "dilamar (atur di ⚙️ Pengaturan > 🎯 Preferensi loker).")[:4000]
    try:
        await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(rows),
                               disable_notification=silent_if_empty)
    except BadRequest:  # biasanya karena ada link yang tidak valid untuk tombol
        await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup([r[:1] for r in rows]),
                               disable_notification=silent_if_empty)


async def daily_check(context: ContextTypes.DEFAULT_TYPE):
    uid, chat_id = context.job.user_id, context.job.chat_id
    try:
        if load_user(uid).get("auto_rekomendasi", True):
            await send_recommendations(context, uid, chat_id, silent_if_empty=True)
    except Exception:
        log.exception("cek rekomendasi harian gagal")
    try:
        await send_followup_reminders(context, uid, chat_id)
    except Exception:
        log.exception("pengingat follow-up gagal")


async def reply_check_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if load_user(context.job.user_id).get("auto_balasan", True):
            await check_replies(context, context.job.user_id, context.job.chat_id)
    except Exception:
        log.exception("cek balasan HR gagal")


# ================================================================ follow-up & balasan HR
async def send_followup_reminders(context, uid: int, chat_id: int):
    due = update_user(uid, lambda d: tracker.pop_due_reminders(d, FOLLOWUP_DAYS, TIMEZONE))
    if not due:
        return
    lines = [f"#{a['id']} {a.get('posisi')} - {a.get('perusahaan')} ({tracker.days_since(a, TIMEZONE)} hari)"
             for a in due]
    rows = [[(f"📨 Follow-up #{a['id']}", f"fu:{a['id']}"), (f"👻 #{a['id']} tidak ada kabar", f"str:{a['id']}:tidak_ada_kabar")]
            for a in due]
    await context.bot.send_message(
        chat_id,
        f"⏰ {len(due)} lamaran belum ada kabar setelah {FOLLOWUP_DAYS} hari:\n\n" + "\n".join(lines) +
        "\n\nMau kirim email follow-up?",
        reply_markup=kb(*rows), disable_notification=True)


def apps_by_account(apps: list[dict]) -> dict:
    """Kelompokkan lamaran per akun Gmail pengirimnya; balasan HR dicari di inbox akun itu."""
    by_account = {}
    for a in apps:
        account = gmail_accounts.get(a.get("dari"))
        if account:
            by_account.setdefault(account, []).append(a)
        else:
            log.warning("akun pengirim lamaran #%s (%s) sudah tidak terdaftar", a["id"], a.get("dari"))
    return by_account


def record_reply(uid: int, app_id: int, reply: dict, info: dict):
    """Simpan balasan & naikkan status. Mengembalikan (lamaran, status berubah?) atau None kalau tidak terkait."""
    def apply(d):
        d["reply_seen"] = ((d.get("reply_seen") or []) + [reply["id"]])[-1000:]
        a = tracker.find(d, app_id)
        if a is None or info.get("jenis") == "tidak_terkait":
            return None
        a.setdefault("balasan", []).append({
            "id": reply["id"], "tanggal": tracker.now(TIMEZONE).isoformat(timespec="minutes"),
            "jenis": info.get("jenis"), "ringkasan": info.get("ringkasan"), "subjek": reply["subject"],
        })
        changed = tracker.apply_reply_status(a, info.get("jenis"), TIMEZONE)
        return dict(a), changed

    return update_user(uid, apply)


def reply_message(app: dict, reply: dict, info: dict, changed: bool) -> str:
    text = (f"📬 Ada balasan untuk lamaran #{app['id']}\n{app.get('posisi')} - {app.get('perusahaan')}\n\n"
            f"Dari: {reply['from']}\nSubjek: {reply['subject']}\n"
            f"Jenis: {JENIS_LABEL.get(info.get('jenis'), info.get('jenis'))}\n\n{info.get('ringkasan') or ''}")
    if info.get("jadwal"):
        text += f"\n\n📅 Jadwal/tenggat: {info['jadwal']}"
    if info.get("perlu_tindakan"):
        text += f"\n👉 Perlu dilakukan: {info['perlu_tindakan']}"
    if changed:
        text += f"\n\nStatus otomatis diubah ke {tracker.STATUS[app['status']]} (bisa diganti lewat /lamaran)."
    return text


async def check_replies(context, uid: int, chat_id: int, manual: bool = False):
    bot = context.bot

    async def say(text):
        if manual:
            await bot.send_message(chat_id, text)

    if not gmail_configured():
        await say(GMAIL_MISSING)
        return
    data = load_user(uid)
    apps = [a for a in data.get("lamaran") or []
            if a.get("status") in tracker.CHECK_REPLIES and a.get("ke")
            and tracker.days_since(a, TIMEZONE) <= 60]
    if not apps:
        await say("Tidak ada lamaran aktif yang perlu dicek balasannya.")
        return

    progress = await bot.send_message(chat_id, "⏳ Mengecek balasan HR di Gmail...") if manual else None
    sent_dates = {a["id"]: tracker.sent_at(a, TIMEZONE) for a in apps}
    seen = set(data.get("reply_seen") or [])
    by_account = apps_by_account(apps)
    replies, failed = [], 0
    for account, account_apps in by_account.items():
        try:
            replies += await asyncio.to_thread(gmail_replies.find_replies, account, account_apps, seen, sent_dates)
        except Exception as e:
            log.exception("gagal membaca balasan Gmail %s", account.address)
            await say(f"Gagal membaca Gmail {account.address}: {e}")
            failed += 1

    found = 0
    for reply in replies:
        app = next(a for a in apps if a["id"] == reply["app_id"])
        try:
            info = await asyncio.to_thread(
                ai_helper.classify_reply, app, reply["from"], reply["subject"], reply["text"])
        except Exception:
            log.exception("gagal mengklasifikasi balasan")
            continue  # tidak ditandai 'seen' supaya dicoba lagi
        result = record_reply(uid, app["id"], reply, info)
        if not result:
            continue
        found += 1
        a, changed = result
        rows = [[("📋 Lihat detail lamaran", f"app:{a['id']}")]]
        if info.get("jenis") == "interview":
            rows.insert(0, [("🎤 Siapkan interview", f"iv:{a['id']}")])
        await bot.send_message(chat_id, reply_message(a, reply, info, changed), reply_markup=kb(*rows),
                               disable_notification=info.get("jenis") not in LOUD_REPLIES)

    if manual and not found and failed < len(by_account):
        await finish_progress(progress, "Belum ada balasan baru dari HR.")
    else:
        await finish_progress(progress)


def followup_view(data: dict, followup: dict) -> dict:
    app = tracker.find(data, followup["app_id"]) or {}
    return {
        "text": f"Draft follow-up untuk {app.get('perusahaan')} ({app.get('posisi')}):\n\n{followup['text']}\n\n"
                f"Mau diubah? Pakai /revisi <instruksi>",
        "reply_markup": kb([(f"✅ Kirim ke {app.get('ke')}", "fusend")],
                           [("🔄 Buat Ulang", f"fu:{followup['app_id']}"), ("❌ Batal", "fucancel")]),
    }


# ================================================================ alur utama lowongan
def source_from_link(url: str | None) -> str | None:
    """'https://www.loker.id/abc' -> 'loker.id'"""
    host = urlparse(url or "").netloc.lower()
    return host.removeprefix("www.") or None


async def process_job(message, context, uid: int, job_text: str, source: str | None = None):
    """source: dari mana lowongan ini (misalnya loker.id), disebut di email lamaran kalau diketahui."""
    data = load_user(uid)
    profile = data.get("profile")
    if not profile:
        await message.reply_text("Kirim CV kamu dulu (PDF/DOCX).")
        return

    msg = await message.reply_text("⏳ Mencocokkan dengan CV kamu...")
    try:
        job = await asyncio.to_thread(ai_helper.analyze_job, profile, job_text)
    except Exception as e:
        log.exception("analisis gagal")
        # Simpan teks lowongannya supaya bisa dicoba lagi tanpa kirim ulang (misalnya saat AI sedang sibuk)
        context.user_data["retry_job_text"] = job_text
        context.user_data["retry_job_source"] = source
        await msg.edit_text(f"Gagal menganalisis lowongan: {e}", reply_markup=kb([("🔄 Coba lagi", "retryjob")]))
        return
    context.user_data["retry_job_text"] = None
    if job.get("bukan_lowongan"):
        await msg.edit_text("Sepertinya itu bukan halaman/teks lowongan kerja. "
                            "Coba copy-paste deskripsi lowongannya langsung ke sini.")
        return

    job["sumber_info"] = source
    job["peringatan"] = job_warnings(data, job, job_text)
    context.user_data.update({
        "job": job, "job_text": job_text, "active_profile": profile, "tailored_cv": None,
        "cover_letter": None, "recipient": None, "awaiting_email": False, "tailor_pending": None, "gap": None,
    })

    if int(job.get("skor") or 0) < TAILOR_THRESHOLD:
        await msg.edit_text(job_summary(job) + f"\n\n🛠️ Skor di bawah {TAILOR_THRESHOLD}, aku sesuaikan CV kamu dulu...")
        await tailor_flow(message, context, uid)
    else:
        await msg.edit_text(job_summary(job), reply_markup=kb(
            [("✍️ Buat Email Lamaran", "cl"), ("🛠️ Sesuaikan CV", "tailor")],
            [("❌ Lewati", "skip")]))


def cv_font_for(data: dict) -> str:
    """Font PDF CV: pilihan pengguna, atau mengikuti font CV asli (default)."""
    choice = data.get("font_cv", "auto")
    return choice if choice != "auto" else cv_fonts.resolve(data.get("font_asli"))


def _point_key(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def par_questions(data: dict, original: dict, result: dict) -> list[dict]:
    """Poin yang perlu ditanyakan hasilnya ke pengguna: belum pernah dijawab atau dilewati, maks 5."""
    originals = {_point_key(p): p for section in ("pengalaman", "proyek")
                 for item in original.get(section) or [] if isinstance(item, dict)
                 for p in (item.get("poin") or []) if isinstance(p, str)}
    known = {_point_key(f.get("poin")) for f in data.get("hasil_poin") or []}
    known |= {_point_key(p) for p in data.get("hasil_dilewati") or []}
    questions, seen = [], set()
    for q in result.get("perlu_hasil") or []:
        if not isinstance(q, dict) or not q.get("poin_asli") or not q.get("pertanyaan"):
            continue
        key = _point_key(q["poin_asli"])
        # AI kadang sedikit mengubah teks poin; cocokkan ke poin asli yang paling mirip
        match = difflib.get_close_matches(key, list(originals), n=1, cutoff=0.8)
        if match:
            key = match[0]
        if key in known or key in seen:
            continue
        seen.add(key)
        questions.append({"poin_asli": originals.get(key, q["poin_asli"]), "pertanyaan": q["pertanyaan"]})
    return questions[:5]


async def tailor_flow(message, context, uid: int):
    data = load_user(uid)
    job = context.user_data["job"]
    msg = await message.reply_text("⏳ Menyesuaikan CV dengan lowongan (metode PAR)...")
    try:
        result = await asyncio.to_thread(ai_helper.tailor_cv, data["profile"], context.user_data["job_text"],
                                         job.get("bahasa_lowongan") or "id", data.get("hasil_poin") or [],
                                         skills=data.get("skill_konfirmasi") or [])
    except Exception as e:
        log.exception("tailoring gagal")
        await msg.edit_text(f"Gagal menyesuaikan CV: {e}", reply_markup=kb(
            [("✍️ Pakai CV Asli & Buat Email Lamaran", "cl")]))
        return
    questions = par_questions(data, data["profile"], result)
    if questions:
        context.user_data["par"] = {"mode": "job", "questions": questions, "i": 0, "answers": [], "result": result}
        await msg.delete()
        await ask_par_question(message, context)
        return
    await finish_tailor(msg, message, context, uid, result)


async def ask_par_question(message, context):
    par = context.user_data["par"]
    q, i, total = par["questions"][par["i"]], par["i"], len(par["questions"])
    intro = ("📝 Supaya poin CV kamu kuat dengan metode PAR (Problem → Action → Result), aku perlu hasil "
             "nyata dari beberapa poin. Aku tidak akan mengarang angka; kalau memang tidak ada, lewati saja "
             "(poin yang dilewati tidak ditanyakan lagi).\n\n") if i == 0 else ""
    await message.reply_text(
        f"{intro}Pertanyaan {i + 1}/{total}\nPoin: \"{q['poin_asli']}\"\n❓ {q['pertanyaan']}\n\n"
        "Ketik jawabannya, contoh: \"waktu input data turun dari 2 jam jadi 15 menit\".",
        reply_markup=kb([("⏭️ Lewati", "par:skip"), ("✅ Cukup, buat CV", "par:done")]))


async def handle_par_answer(message, context, uid: int, text: str):
    par = context.user_data["par"]
    par["answers"].append({"poin": par["questions"][par["i"]]["poin_asli"], "hasil": text.strip()})
    await next_par_question(message, context, uid)


async def next_par_question(message, context, uid: int):
    par = context.user_data["par"]
    par["i"] += 1
    if par["i"] < len(par["questions"]):
        await ask_par_question(message, context)
    else:
        await complete_par(message, context, uid)


async def btn_par(query, context, uid, data, arg):
    """par:skip, par:done"""
    par = context.user_data.get("par")
    await query.edit_message_reply_markup(None)
    if not par:
        await query.message.reply_text("Sesi pertanyaan ini sudah selesai.")
        return
    if par.get("mode") == "job" and not context.user_data.get("job"):
        context.user_data["par"] = None
        await query.message.reply_text("Sesi lowongan sudah habis. Kirim ulang lowongannya ya.")
        return
    if arg == "skip":
        point = par["questions"][par["i"]]["poin_asli"]
        update_user(uid, lambda d: d.__setitem__("hasil_dilewati", ((d.get("hasil_dilewati") or []) + [point])[-200:]))
        await next_par_question(query.message, context, uid)
    else:
        await complete_par(query.message, context, uid)


def save_facts(uid: int, answers: list[dict]) -> dict:
    """Simpan jawaban hasil sebagai fakta; dipakai lagi untuk penyesuaian CV & perbaikan PAR berikutnya."""
    def apply(d):
        new_keys = {_point_key(a["poin"]) for a in answers}
        d["hasil_poin"] = [f for f in d.get("hasil_poin") or [] if _point_key(f.get("poin")) not in new_keys] + answers
        return d
    return update_user(uid, apply)


async def complete_par(message, context, uid: int):
    par = context.user_data.pop("par")
    answers = par["answers"]
    if par.get("mode") == "master":
        await finish_master_par(message, context, uid, par)
        return
    if not answers:
        msg = await message.reply_text("⏳ Membuat CV...")
        await finish_tailor(msg, message, context, uid, par["result"])
        return

    data = save_facts(uid, answers)
    job = context.user_data["job"]
    msg = await message.reply_text(f"⏳ Menyusun ulang CV dengan {len(answers)} hasil dari kamu...")
    try:
        result = await asyncio.to_thread(ai_helper.tailor_cv, data["profile"], context.user_data["job_text"],
                                         job.get("bahasa_lowongan") or "id", data["hasil_poin"], False,
                                         skills=data.get("skill_konfirmasi") or [])
    except Exception:
        log.exception("tailoring ulang gagal, pakai hasil pertama")
        result = par["result"]
    await finish_tailor(msg, message, context, uid, result)


def _skill_key(skill: str) -> str:
    return " ".join(str(skill or "").lower().split())


def remaining_gaps(data: dict, missing: list) -> list[str]:
    """Skill yang kurang dan belum pernah ditanyakan (belum dikonfirmasi punya / belum punya)."""
    answered = {_skill_key(k.get("skill")) for k in data.get("skill_konfirmasi") or []}
    answered |= {_skill_key(x) for x in data.get("skill_belum") or []}
    gaps = []
    for skill in missing or []:
        if isinstance(skill, str) and skill.strip() and _skill_key(skill) not in answered | {_skill_key(g) for g in gaps}:
            gaps.append(skill.strip())
    return gaps[:5]


async def finish_tailor(msg, message, context, uid: int, result: dict, note: str = ""):
    """Nilai ulang CV yang disesuaikan lalu tampilkan skornya. PDF baru dibuat setelah pengguna setuju."""
    data = load_user(uid)
    job = context.user_data["job"]
    job.setdefault("skor_awal", int(job.get("skor") or 0))
    confirmed = [k["skill"] for k in data.get("skill_konfirmasi") or []]
    try:
        tailored = ai_helper.guard_profile(data["profile"], result.get("profile") or {}, confirmed)
        await msg.edit_text("⏳ Menilai ulang CV yang sudah disesuaikan...")
        new_job = await asyncio.to_thread(ai_helper.analyze_job, tailored, context.user_data["job_text"])
    except Exception as e:
        log.exception("tailoring gagal")
        await msg.edit_text(f"Gagal menyesuaikan CV: {e}", reply_markup=kb(
            [("✍️ Pakai CV Asli & Buat Email Lamaran", "cl")]))
        return
    pending = {
        "tailored": tailored, "result": result, "new_score": int(new_job.get("skor") or job["skor_awal"]),
        "skill_kurang": new_job.get("skill_kurang") or [], "skill_cocok": new_job.get("skill_cocok") or [],
    }
    context.user_data["tailor_pending"] = pending
    text, markup = tailor_preview(data, job, pending, note)
    await msg.edit_text(text, reply_markup=markup)


def tailor_preview(data: dict, job: dict, pending: dict, note: str = ""):
    gaps = remaining_gaps(data, pending["skill_kurang"])
    changes = "\n".join(f"• {c}" for c in pending["result"].get("perubahan") or []) or "-"
    text = (f"{note}🛠️ Hasil penyesuaian CV (metode PAR)\nSkor: {job['skor_awal']} → {pending['new_score']}\n\n"
            f"✅ Cocok: {', '.join(pending['skill_cocok']) or '-'}\n"
            f"⚠️ Masih kurang: {', '.join(pending['skill_kurang']) or '-'}\n\n"
            f"Yang diubah:\n{changes}")
    if gaps:
        text += ("\n\n🚀 Mau skor lebih tinggi? Aku tanyakan pengalaman nyata kamu yang belum tertulis di CV "
                 f"({', '.join(gaps)}). Tidak ada yang dikarang.")
    elif pending["skill_kurang"]:
        text += ("\n\nℹ️ Kekurangan yang tersisa sudah kamu jawab belum pernah, jadi ini skor maksimal yang jujur "
                 "untuk lowongan ini. Skill itu bisa jadi bahan belajar.")
    if pending["new_score"] < 50:
        text += ("\n\n⚠️ Skornya masih rendah. Lowongan ini mungkin memang kurang cocok, pertimbangkan untuk dilewati.")
    text += "\n\nPDF belum dibuat. Pilih dulu:"
    rows = [[("✅ Pakai skor ini, buat CV", "tl:ok")]]
    if gaps:
        rows.append([("🚀 Tingkatkan lagi", "tl:more")])
    rows.append([("↩️ Pakai CV asli", "orig"), ("❌ Lewati", "skip")])
    return text[:4000], kb(*rows)


async def btn_tailor_result(query, context, uid, data, arg):
    """tl:ok (buat PDF dari hasil yang ditampilkan), tl:more (tanya skill yang kurang)"""
    pending = context.user_data.get("tailor_pending")
    await query.edit_message_reply_markup(None)
    if not pending:
        await query.message.reply_text("Hasil penyesuaian ini sudah tidak berlaku. Tekan 🛠️ Sesuaikan CV lagi.")
        return
    if arg == "more":
        gaps = remaining_gaps(data, pending["skill_kurang"])
        if not gaps:
            await query.message.reply_text("Semua kekurangan sudah ditanyakan.")
            return
        context.user_data["gap"] = {"questions": gaps, "i": 0, "answers": [], "no": [], "awaiting_story": False}
        await ask_gap_question(query.message, context)
        return

    job = context.user_data["job"]
    lang = job.get("bahasa_lowongan") or "id"
    tailored = pending["tailored"]
    msg = await query.message.reply_text("⏳ Membuat PDF CV...")
    try:
        filename = f"CV_{safe_name(tailored.get('nama') or 'Kandidat')}_{safe_name(job.get('posisi') or 'Lamaran')}.pdf"
        path = user_dir(uid) / filename
        font = await asyncio.to_thread(cv_builder.build_pdf, tailored, str(path), lang, cv_font_for(data))
    except Exception as e:
        log.exception("membuat PDF gagal")
        await msg.edit_text(f"Gagal membuat PDF: {e}", reply_markup=kb([("✅ Coba lagi", "tl:ok")]))
        return
    job["skor"] = pending["new_score"]
    job["skill_kurang"] = pending["skill_kurang"]
    context.user_data.update({"active_profile": tailored, "tailored_cv": str(path), "tailor_pending": None})
    font_note = f"Font: {cv_fonts.label(font)}"
    if font != cv_font_for(data):
        font_note += f" ({cv_fonts.label(cv_font_for(data))} gagal diambil, cek koneksi VPS)"
    await msg.delete()
    with open(path, "rb") as f:
        await query.message.reply_document(
            f, filename=path.name,
            caption=(f"CV untuk {job.get('posisi')} (skor {pending['new_score']}). Cek dulu ya. Isinya hanya dari CV "
                     f"asli kamu dan hal yang kamu konfirmasi.\n{font_note}"),
            reply_markup=kb([("✍️ Buat Email Lamaran", "cl")],
                            [("↩️ Pakai CV Asli", "orig"), ("❌ Lewati", "skip")]))


# ---- 🚀 Tingkatkan lagi: tanya pengalaman nyata yang belum tertulis di CV
async def ask_gap_question(message, context):
    gap = context.user_data["gap"]
    skill, i, total = gap["questions"][gap["i"]], gap["i"], len(gap["questions"])
    await message.reply_text(
        f"🚀 Pertanyaan {i + 1}/{total}\nLowongan ini meminta: {skill}\n\n"
        "Kamu pernah memakai atau punya pengalaman dengan ini? Jawab jujur ya, karena yang kamu jawab \"pernah\" "
        "akan masuk ke CV dan bisa ditanyakan saat interview.",
        reply_markup=kb([("✅ Pernah", "gap:yes"), ("❌ Belum", "gap:no")], [("Cukup, nilai ulang", "gap:done")]))


async def btn_gap(query, context, uid, data, arg):
    """gap:yes / gap:no / gap:done, lalu gap:addcv / gap:noadd (tambahkan ke CV asli?)"""
    await query.edit_message_reply_markup(None)
    if arg in ("addcv", "noadd"):
        skills = context.user_data.pop("gap_add", None) or []
        if arg == "addcv" and skills:
            added = edit_profile(uid, lambda prof: pe.list_add(prof, "skill", skills))
            await query.message.reply_text(f"✅ {added} skill ditambahkan ke 📄 CV Saya.")
        else:
            await query.message.reply_text("Oke, tidak ditambahkan ke CV asli. Tetap dipakai saat menyesuaikan CV.")
        return
    gap = context.user_data.get("gap")
    if not gap or not context.user_data.get("job"):
        context.user_data["gap"] = None
        await query.message.reply_text("Sesi pertanyaan ini sudah selesai.")
        return
    skill = gap["questions"][gap["i"]]
    if arg == "yes":
        gap["awaiting_story"] = True
        await query.message.reply_text(
            f"Ceritakan singkat di mana dan untuk apa kamu memakai {skill}.\n"
            "Contoh: \"Pakai Docker untuk deploy aplikasi kasir ke VPS kantor\".")
    elif arg == "no":
        gap["no"].append(skill)
        await next_gap_question(query.message, context, uid)
    else:
        await complete_gap(query.message, context, uid)


async def handle_gap_story(message, context, uid: int, text: str):
    gap = context.user_data["gap"]
    gap["answers"].append({"skill": gap["questions"][gap["i"]], "cerita": text.strip()})
    gap["awaiting_story"] = False
    await next_gap_question(message, context, uid)


async def next_gap_question(message, context, uid: int):
    gap = context.user_data["gap"]
    gap["i"] += 1
    if gap["i"] < len(gap["questions"]):
        await ask_gap_question(message, context)
    else:
        await complete_gap(message, context, uid)


async def complete_gap(message, context, uid: int):
    gap = context.user_data.pop("gap")
    answers, no = gap["answers"], gap["no"]

    def save(d):
        keys = {_skill_key(a["skill"]) for a in answers}
        d["skill_konfirmasi"] = [k for k in d.get("skill_konfirmasi") or [] if _skill_key(k.get("skill")) not in keys] + answers
        d["skill_belum"] = ((d.get("skill_belum") or []) + no)[-200:]
        return d

    data = update_user(uid, save)
    job = context.user_data["job"]
    if not answers:
        pending = context.user_data.get("tailor_pending")
        if not pending:
            await message.reply_text("Hasil penyesuaian sudah tidak berlaku. Tekan 🛠️ Sesuaikan CV lagi.")
            return
        text, markup = tailor_preview(data, job, pending, "Oke, dicatat. ")
        await message.reply_text(text, reply_markup=markup)
        return

    names = [a["skill"] for a in answers]
    context.user_data["gap_add"] = names
    await message.reply_text(
        f"Tambahkan {', '.join(names)} ke 📄 CV Saya juga? Kalau ya, skill ini ikut masuk CV asli dan dipakai di "
        "semua lamaran berikutnya.",
        reply_markup=kb([("✅ Ya, tambahkan", "gap:addcv"), ("Nanti saja", "gap:noadd")]))
    msg = await message.reply_text(f"⏳ Menyusun ulang CV dengan {len(answers)} pengalaman tambahan dari kamu...")
    try:
        result = await asyncio.to_thread(ai_helper.tailor_cv, data["profile"], context.user_data["job_text"],
                                         job.get("bahasa_lowongan") or "id", data.get("hasil_poin") or [], False,
                                         skills=data["skill_konfirmasi"])
    except Exception as e:
        log.exception("tailoring ulang gagal")
        await msg.edit_text(f"Gagal menyusun ulang CV: {e}", reply_markup=kb([("✅ Pakai hasil sebelumnya", "tl:ok")]))
        return
    await finish_tailor(msg, message, context, uid, result)


# ================================================================ gambar lowongan
async def process_job_image(message, context, uid: int, tg_file, mime: str):
    if not load_user(uid).get("profile"):
        await message.reply_text("Kirim CV kamu dulu (PDF/DOCX), baru kirim gambar lowongannya.")
        return
    msg = await message.reply_text("⏳ Membaca gambar lowongan...")
    try:
        file = await tg_file.get_file()
        image = bytes(await file.download_as_bytearray())
        job_text = await asyncio.to_thread(ai_helper.read_job_image, image, mime, message.caption or "")
    except Exception as e:
        log.exception("gagal membaca gambar lowongan")
        await msg.edit_text(f"Gagal membaca gambar: {e}\nCoba kirim teks lowongannya saja.")
        return
    if "BUKAN_LOWONGAN" in job_text[:100] or len(job_text.strip()) < 50:
        await msg.edit_text("Gambar ini sepertinya bukan lowongan kerja, atau tulisannya tidak terbaca. "
                            "Coba kirim gambar yang lebih jelas atau paste teks lowongannya.")
        return
    await msg.delete()
    await process_job(message, context, uid, job_text)


@only_allowed
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_job_image(update.message, context, update.effective_user.id,
                            update.message.photo[-1], "image/jpeg")


# ================================================================ upload CV
@only_allowed
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    ext = Path(doc.file_name or "").suffix.lower()
    if ext in IMAGE_EXT:
        if (doc.file_size or 0) > MAX_IMAGE_BYTES:
            await update.message.reply_text("Gambarnya terlalu besar. Kirim sebagai foto biasa (bukan file) ya.")
            return
        await process_job_image(update.message, context, update.effective_user.id, doc, IMAGE_EXT[ext])
        return
    if ext == ".zip":
        await receive_backup(update.message, context, update.effective_user.id, doc)
        return
    if ext not in (".pdf", ".docx"):
        await update.message.reply_text("Kirim CV dalam format PDF/DOCX, gambar lowongan (JPG/PNG), "
                                        "atau file backup (.zip).")
        return

    uid = update.effective_user.id
    original_dir = user_dir(uid) / "original"
    original_dir.mkdir(exist_ok=True)
    for old in original_dir.iterdir():
        old.unlink()
    path = original_dir / f"{safe_name(Path(doc.file_name).stem, 60)}{ext}"

    msg = await update.message.reply_text("⏳ Membaca CV...")
    try:
        file = await doc.get_file()
        await file.download_to_drive(path)
        text = await asyncio.to_thread(cv_parser.extract_text, str(path))
        if len(text) < 100:
            await msg.edit_text("Teks CV hampir kosong. Kemungkinan PDF hasil scan/gambar. "
                                "Coba export ulang CV dari Word/Canva/Google Docs sebagai PDF.")
            return
        await msg.edit_text("⏳ Menganalisis isi CV...")
        profile = await asyncio.to_thread(ai_helper.extract_profile, text)
        font_raw = await asyncio.to_thread(cv_parser.detect_font, str(path))
    except Exception as e:
        log.exception("gagal proses CV")
        await msg.edit_text(f"Gagal memproses CV: {e}")
        return

    data = load_user(uid)
    data.update({"cv_path": str(path), "profile": profile, "font_asli": font_raw,
                 "profile_edited": False, "cv_master_pdf": None})
    save_user(uid, data)
    context.user_data.update({"job": None, "active_profile": profile, "tailored_cv": None})
    await msg.edit_text(
        f"✅ CV tersimpan, {profile.get('nama') or 'kamu'}!\n"
        f"Skill terdeteksi: {', '.join((profile.get('skill') or [])[:10]) or '-'}\n"
        f"Font CV: {cv_fonts.clean_name(font_raw) if font_raw else 'tidak terdeteksi'}"
        f" (CV yang disesuaikan memakai {cv_fonts.label(cv_fonts.resolve(font_raw))})\n\n"
        "Sekarang kirim link / teks lowongan, atau ketik /rekomendasi.")


# ================================================================ teks masuk
MENU_ACTIONS = {
    "🎯 Rekomendasi": rekomendasi, "📂 Lamaran": lamaran, "📬 Cek Balasan": cekbalasan,
    "📊 Statistik": statistik, "📄 CV Saya": profil, "👤 Profil": profil, "⚙️ Pengaturan": pengaturan,
}


@only_allowed
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text in MENU_ACTIONS:
        context.user_data.update({"awaiting_email": False, "gmail_form": None, "ai_form": None,
                                  "font_form": False, "par": None, "cv_form": None, "gap": None,
                                  "pref_form": None, "iv": None})
        await MENU_ACTIONS[text](update, context)
        return

    if context.user_data.get("gmail_form"):
        await handle_gmail_form(update.message, context, uid, text)
        return

    if context.user_data.get("ai_form"):
        await handle_ai_form(update.message, context, uid, text)
        return

    if context.user_data.get("font_form"):
        await handle_font_form(update.message, context, uid, text)
        return

    if context.user_data.get("cv_form"):
        await handle_cv_form(update.message, context, uid, text)
        return

    if context.user_data.get("par"):
        await handle_par_answer(update.message, context, uid, text)
        return

    gap = context.user_data.get("gap")
    if gap and gap.get("awaiting_story"):
        await handle_gap_story(update.message, context, uid, text)
        return

    if context.user_data.get("pref_form"):
        await handle_pref_form(update.message, context, uid, text)
        return

    if context.user_data.get("iv"):
        await handle_iv_answer(update.message, context, uid, text)
        return

    if context.user_data.get("awaiting_email"):
        if not EMAIL_RE.match(text):
            await update.message.reply_text("Format email tidak valid, coba lagi.")
            return
        context.user_data["awaiting_email"] = False
        context.user_data["recipient"] = text
        await show_send_confirmation(update.message, context, uid)
        return

    url = URL_RE.search(text)
    if url:
        msg = await update.message.reply_text("⏳ Membuka link lowongan...")
        try:
            job_text = await asyncio.to_thread(job_fetcher.fetch_job_text, url.group(0))
            if len(job_text) < 300:
                raise ValueError("isi halaman terlalu sedikit")
        except Exception as e:
            await msg.edit_text(f"Tidak bisa membuka link ({e}). Situs seperti LinkedIn/JobStreet "
                                "sering memblokir bot, jadi copy-paste saja teks lowongannya ke sini.")
            return
        await msg.delete()
    elif len(text) >= 150:
        job_text = text
    else:
        await update.message.reply_text("Teksnya terlalu pendek. Kirim link lowongan atau paste deskripsi lengkapnya.")
        return

    await process_job(update.message, context, uid, job_text, source_from_link(url.group(0)) if url else None)


# ================================================================ kirim email
def cover_letter_buttons():
    return kb([("📧 Kirim via Gmail", "send"), ("🔄 Buat Ulang", "cl")])


def email_subject(job: dict, name: str) -> str:
    subject = f"Lamaran {job.get('posisi') or 'Pekerjaan'}"
    return f"{subject} - {name}" if name else subject


def attachment_path(context, uid: int) -> str | None:
    """CV yang dilampirkan: versi yang disesuaikan, atau CV asli (dibuat ulang kalau diubah lewat 📄 CV Saya)."""
    if context.user_data.get("tailored_cv"):
        return context.user_data["tailored_cv"]
    data = load_user(uid)
    if data.get("profile_edited") and data.get("cv_master_pdf"):
        return data["cv_master_pdf"]
    return data.get("cv_path")


async def show_send_confirmation(message, context, uid: int):
    job = context.user_data["job"]
    name = (load_user(uid).get("profile") or {}).get("nama") or ""
    attach = attachment_path(context, uid)
    cv_type = "CV yang sudah disesuaikan" if context.user_data.get("tailored_cv") else "CV asli"
    accounts = gmail_accounts.load()
    if len(accounts) == 1:
        buttons = [[("✅ Ya, kirim", "confirm:0")]]
    else:
        default = default_account(load_user(uid))
        order = sorted(range(len(accounts)), key=lambda i: accounts[i] != default)
        buttons = [[(f"{'⭐' if accounts[i] == default else '✅'} Kirim dari {accounts[i].address}", f"confirm:{i}")]
                   for i in order]
    warnings = []
    if job.get("penipuan_serius"):
        warnings.append("🚨 Lowongan ini punya tanda penipuan serius (lihat ringkasan lowongan). Yakin tetap kirim?")
    dups = rules.find_duplicates(load_user(uid).get("lamaran") or [], job.get("posisi"), job.get("perusahaan"))
    if dups:
        warnings.append(f"🔁 Kamu sudah pernah melamar ke {job.get('perusahaan')} "
                        f"({len(dups)}x, terakhir #{dups[-1]['id']}).")
    now = tracker.now(TIMEZONE)
    if load_user(uid).get("jadwal_kirim", True) and not rules.in_work_hours(now):
        warnings.append(f"🕗 Sekarang di luar jam kerja, jadi email dijadwalkan terkirim "
                        f"{rules.when_label(rules.next_work_time(now), now)} (bisa dikirim sekarang setelahnya).")
    await message.reply_text(
        "".join(f"{w}\n\n" for w in warnings) +
        f"Konfirmasi pengiriman:\n\n"
        f"Ke: {context.user_data['recipient']}\n"
        f"Subjek: {email_subject(job, name)}\n"
        f"Lampiran: {Path(attach).name if attach else '-'} ({cv_type})\n"
        f"Isi: email lamaran di atas\n\n"
        f"Posisi: {job.get('posisi')} - {job.get('perusahaan')}" + (
            f"\nDari: {accounts[0].address}" if len(accounts) == 1 else "\n\nPilih akun Gmail pengirimnya:"),
        reply_markup=kb(*buttons, [("✏️ Ganti email tujuan", "editto"), ("❌ Batal", "cancel")]))


# ================================================================ tombol
# Setiap tombol punya callback_data "<nama>" atau "<nama>:<argumen>". Satu fungsi per tombol,
# dipetakan di BUTTONS. Semua fungsi menerima (query, context, uid, data, arg).
async def btn_skip(query, context, uid, data, arg):
    await query.edit_message_reply_markup(None)
    await query.message.reply_text("Oke, dilewati. Kirim lowongan berikutnya kapan saja.")


async def btn_gaya(query, context, uid, data, style):
    update_user(uid, lambda d: d.__setitem__("gaya", style))
    markup = kb([("🔄 Buat ulang email lamaran", "cl")]) if context.user_data.get("job") else None
    await safe_edit(query.message, f"✅ Gaya email lamaran diubah ke {GAYA_LABEL.get(style)}.", markup)


async def btn_settings(query, context, uid, data, arg):
    """set:gaya, set:tog:<key>, set:back"""
    parts = arg.split(":")
    if parts[0] == "gaya":
        text, markup = gaya_view(data)
        await safe_edit(query.message, text, markup)
        return
    if parts[0] == "tog" and parts[1] in TOGGLES:
        update_user(uid, lambda d: d.__setitem__(parts[1], not d.get(parts[1], True)))
    text, markup = settings_view(load_user(uid))
    await safe_edit(query.message, text, markup)


def accounts_view(data: dict):
    accounts = gmail_accounts.load()
    default = default_account(data)
    if accounts:
        lines = [f"{'⭐' if a == default else '•'} {a.address}{' (.env)' if a.from_env else ''}" for a in accounts]
        text = ("📧 Akun Gmail\n\n" + "\n".join(lines) +
                "\n\n⭐ = akun default saat mengirim lamaran. Job alert dibaca dari semua akun.\n"
                "Pilih akun untuk jadikan default, ganti app password, atau hapus.")
    else:
        text = "📧 Akun Gmail\n\nBelum ada akun. Tambahkan akun untuk membaca job alert dan mengirim lamaran."
    rows = [[(a.address, f"acc:{i}")] for i, a in enumerate(accounts)]
    return text, kb(*rows, [("➕ Tambah akun", "acc:add")], [("⬅️ Pengaturan", "set:back")])


def account_detail_view(data: dict, i: int, account: gmail_accounts.Account):
    is_default = account == default_account(data)
    text = (f"📧 {account.address}\n"
            f"{'⭐ Akun default' if is_default else 'Bukan akun default'}\n")
    rows = [] if is_default else [[("⭐ Jadikan default", f"acc:{i}:def")]]
    if account.from_env:
        text += "\nAkun ini diatur di file .env, jadi app password dan penghapusannya diubah lewat .env."
    else:
        rows += [[("🔑 Ganti app password", f"acc:{i}:pw")], [("🗑️ Hapus akun", f"acc:{i}:del")]]
    return text, kb(*rows, [("⬅️ Daftar akun", "acc")])


ADD_ACCOUNT_HELP = (
    "Ketik app password Gmail untuk {address} (16 huruf, spasi boleh).\n\n"
    "Cara buat: aktifkan verifikasi 2 langkah di akun Google, lalu buka "
    "https://myaccount.google.com/apppasswords dan buat app password baru.\n\n"
    "Pesan berisi password akan langsung aku hapus dari chat. Ketik batal untuk membatalkan.")


async def btn_accounts(query, context, uid, data, arg):
    """acc, acc:add, acc:<i>, acc:<i>:def, acc:<i>:pw, acc:<i>:del, acc:delok"""
    if not ALLOWED:
        await query.message.reply_text("Isi ALLOWED_USER_IDS di .env dulu supaya orang lain tidak bisa "
                                       "melihat atau mengubah akun Gmail kamu.")
        return
    parts = arg.split(":") if arg else []
    if not parts:
        text, markup = accounts_view(data)
        await safe_edit(query.message, text, markup)
        return
    if parts[0] == "add":
        context.user_data["gmail_form"] = {"step": "address"}
        await query.message.reply_text("Ketik alamat Gmail yang mau ditambahkan (atau ketik batal):")
        return
    if parts[0] == "delok":
        address = context.user_data.pop("gmail_delete", None)
        if address:
            gmail_accounts.remove(address)
            await query.message.reply_text(f"🗑️ {address} dihapus.")
        text, markup = accounts_view(load_user(uid))
        await safe_edit(query.message, text, markup)
        return

    accounts = gmail_accounts.load()
    i = int(parts[0])
    if i >= len(accounts):
        text, markup = accounts_view(data)
        await safe_edit(query.message, "Daftar akun sudah berubah, pilih lagi.\n\n" + text, markup)
        return
    account = accounts[i]
    action = parts[1] if len(parts) > 1 else ""
    if action == "def":
        update_user(uid, lambda d: d.__setitem__("gmail_default", account.address))
        data = load_user(uid)
    elif action == "pw" and not account.from_env:
        context.user_data["gmail_form"] = {"step": "password", "address": account.address, "edit": True}
        await query.message.reply_text(ADD_ACCOUNT_HELP.format(address=account.address))
        return
    elif action == "del" and not account.from_env:
        context.user_data["gmail_delete"] = account.address
        await safe_edit(query.message,
                        f"Hapus {account.address} dari bot?\n\nLamaran yang dikirim dari akun ini tidak "
                        f"bisa dicek balasannya atau di-follow-up sampai akunnya ditambahkan lagi.",
                        kb([("🗑️ Ya, hapus", "acc:delok"), ("❌ Batal", f"acc:{i}")]))
        return
    text, markup = account_detail_view(data, i, account)
    await safe_edit(query.message, text, markup)


async def handle_gmail_form(message, context, uid: int, text: str):
    """Isian tambah akun / ganti app password, satu langkah per pesan."""
    form = context.user_data["gmail_form"]
    if text.lower() == "batal":
        context.user_data["gmail_form"] = None
        await message.reply_text("Dibatalkan.")
        return

    if form["step"] == "address":
        address = text.lower()
        if not EMAIL_RE.match(address):
            await message.reply_text("Format email tidak valid, coba lagi (atau ketik batal).")
            return
        if gmail_accounts.get(address):
            await message.reply_text(f"{address} sudah terdaftar. Pilih akunnya di 📧 Akun Gmail untuk "
                                     "mengganti app password.")
            context.user_data["gmail_form"] = None
            return
        form.update({"step": "password", "address": address, "edit": False})
        await message.reply_text(ADD_ACCOUNT_HELP.format(address=address))
        return

    # langkah password: hapus pesannya dari chat secepatnya. Setelah itu kirim pesan baru,
    # bukan reply, karena pesan yang dibalas sudah tidak ada.
    password = text.replace(" ", "")
    try:
        await message.delete()
    except BadRequest:
        log.warning("pesan berisi app password tidak bisa dihapus")
    if not APP_PASSWORD_RE.fullmatch(password):
        await context.bot.send_message(message.chat_id, "App password harus 16 huruf. Coba kirim lagi (atau ketik batal).")
        return
    address = form["address"]
    msg = await context.bot.send_message(message.chat_id, f"⏳ Mencoba login ke {address}...")
    try:
        await asyncio.to_thread(gmail_accounts.verify, address, password)
    except Exception as e:
        log.info("verifikasi akun %s gagal: %s", address, e)
        await msg.edit_text(f"❌ Login ke {address} gagal: {e}\n\nPastikan app password benar dan IMAP aktif "
                            "(Gmail > Setelan > Penerusan dan POP/IMAP). Kirim app password lagi, atau ketik batal.")
        return
    gmail_accounts.save(address, password)
    context.user_data["gmail_form"] = None
    text, markup = accounts_view(load_user(uid))
    await msg.edit_text(f"✅ {address} {'diperbarui' if form['edit'] else 'ditambahkan'}.\n\n" + text,
                        reply_markup=markup)


# Setting yang dipilih dari daftar model: kode di callback_data -> nama setting
MODEL_TARGETS = {
    "m": lambda: ai_helper.PROVIDERS[ai_helper.provider()]["model"],
    "f": lambda: ai_helper.PROVIDERS[ai_helper.provider()]["fallback"],
    "v": lambda: "OLLAMA_VISION_MODEL",
}
MODEL_TARGET_LABEL = {"m": "model utama", "f": "model cadangan", "v": "model untuk membaca foto"}
MAX_MODEL_BUTTONS = 30
CLAUDE_LOGIN_STEPS = (
    "Cara login (sekali saja, di VPS, sebagai user yang menjalankan bot):\n"
    "1. Install CLI ant dari https://github.com/anthropics/anthropic-cli/releases\n"
    "   (pilih file linux_amd64 atau linux_arm64, ekstrak, taruh ant di /usr/local/bin)\n"
    "2. Jalankan: ant auth login --no-browser\n"
    "   Buka link yang muncul, login ke akun Claude Console, lalu tempel kodenya ke terminal.\n"
    "3. Kalau ada baris ANTHROPIC_API_KEY di .env, hapus lalu ./bot.sh restart\n"
    "4. Tekan 🔄 Cek login.\n\n"
    "Catatan: ini login akun Claude Console (pemakaian API, bayar sesuai pemakaian), bukan kuota "
    "langganan Claude Pro/Max. Login kedaluwarsa berkala; kalau bot bilang login kedaluwarsa, ulangi langkah 2.")


def ai_view(note: str = ""):
    name = ai_helper.provider()
    info = ai_helper.PROVIDERS.get(name)
    if not info:
        return (f"{note}🤖 Model AI\n\nProvider '{name}' tidak dikenal. Pilih provider.",
                kb([("🔁 Pilih provider", "ai:prov")], [("⬅️ Pengaturan", "set:back")]))
    lines = [f"Provider: {info['label']}"]
    rows = [[("🔁 Ganti provider", "ai:prov")]]
    if name == "openai":
        lines.append(f"Alamat API: {ai_helper.setting('OPENAI_BASE_URL')}")
        rows.append([("🌐 Ganti alamat API", "ai:url")])
    lines.append(f"Model: {ai_helper.current_model() or 'belum dipilih'}")
    rows.append([("🧠 Pilih model", "ai:model")])
    if info["fallback"]:
        lines.append(f"Cadangan saat sibuk: {ai_helper.setting(info['fallback']) or 'tidak ada'}")
        rows.append([("🛟 Model cadangan", "ai:fb")])
    if name == "ollama":
        lines += [f"Alamat: {ai_helper.setting('OLLAMA_URL')}",
                  f"Model untuk foto: {ai_helper.setting('OLLAMA_VISION_MODEL')}"]
        rows.append([("🖼️ Model untuk foto", "ai:vis")])
    if name == "claude" and ai_helper.claude_uses_login():
        lines.append("Masuk dengan: login akun Claude Console (ant auth login)")
        rows.append([("🔄 Cek login", "ai:auth:login"), ("🔑 Pakai API key", "ai:auth:key")])
    elif info["key"]:
        key = ai_helper.setting(info["key"])
        lines.append(f"API key: {app_settings.masked(key) if key or name != 'openai' else 'tidak dipakai'}")
        rows.append([("🔑 Ganti API key", "ai:key")])
        if name == "claude":
            rows.append([("🔐 Pakai login (tanpa API key)", "ai:auth:login")])
    rows += [[("🧪 Tes AI", "ai:test")], [("⬅️ Pengaturan", "set:back")]]
    return f"{note}🤖 Model AI\n\n" + "\n".join(lines), kb(*rows)


def api_key_prompt(name: str):
    info = ai_helper.PROVIDERS[name]
    where = f"\nBuat di: {info['key_url']}" if info["key_url"] else ""
    text = (f"Kirim API key {name.capitalize() if name != 'openai' else 'untuk ' + ai_helper.setting('OPENAI_BASE_URL')}."
            f"{where}\n\nPesan berisi API key akan langsung aku hapus dari chat. Ketik batal untuk membatalkan.")
    markup = kb([("🚫 Tanpa API key (server lokal)", "ai:nokey")]) if name == "openai" else None
    return text, markup


async def ask_api_key(message, context, name: str):
    context.user_data["ai_form"] = {"step": "key", "provider": name}
    text, markup = api_key_prompt(name)
    await message.reply_text(text, reply_markup=markup)


def url_presets_view():
    rows = [[(label, f"ai:url:{i}")] for i, (label, _) in enumerate(ai_helper.OPENAI_PRESETS)]
    return ("Pilih layanan AI yang kompatibel dengan OpenAI, atau ketik alamat API lain:",
            kb(*rows, [("✏️ Alamat lain", "ai:url:custom")], [("⬅️ Kembali", "ai")]))


async def btn_ai(query, context, uid, data, arg):
    """ai, ai:prov[:<nama>], ai:url[:<i>|custom], ai:model|fb|vis, ai:set:<kode>:<i|none>, ai:type:<kode>,
    ai:key, ai:nokey, ai:auth:login|key, ai:test"""
    if not ALLOWED:
        await query.message.reply_text("Isi ALLOWED_USER_IDS di .env dulu supaya orang lain tidak bisa "
                                       "melihat atau mengubah pengaturan AI dan API key kamu.")
        return
    parts = arg.split(":") if arg else []
    cmd = parts[0] if parts else ""

    if cmd == "prov" and len(parts) == 1:
        current = ai_helper.provider()
        rows = [[(f"{'✅ ' if key == current else ''}{info['label']}", f"ai:prov:{key}")]
                for key, info in ai_helper.PROVIDERS.items()]
        await safe_edit(query.message, "Pilih provider AI:", kb(*rows, [("⬅️ Kembali", "ai")]))
        return
    if cmd == "prov":
        name = parts[1]
        if name not in ai_helper.PROVIDERS:
            return
        app_settings.set("AI_PROVIDER", name)
        note = f"✅ Provider diganti ke {ai_helper.PROVIDERS[name]['label']}.\n\n"
        if name == "openai":
            text, markup = url_presets_view()
            await safe_edit(query.message, note + text, markup)
            return
        if name == "claude" and not ai_helper.claude_uses_login() and not ai_helper.setting("ANTHROPIC_API_KEY"):
            await safe_edit(query.message, note + "Masuk ke Claude dengan cara apa?",
                            kb([("🔐 Login akun Claude Console", "ai:auth:login")],
                               [("🔑 API key", "ai:auth:key")]))
            return
        await safe_edit(query.message, *ai_view(note))
        key = ai_helper.PROVIDERS[name]["key"]
        if name != "claude" and key and not ai_helper.setting(key):
            await ask_api_key(query.message, context, name)
        return

    if cmd == "url" and len(parts) == 1:
        await safe_edit(query.message, *url_presets_view())
        return
    if cmd == "url" and parts[1] == "custom":
        context.user_data["ai_form"] = {"step": "url"}
        await query.message.reply_text("Ketik alamat API (base URL), contoh: https://openrouter.ai/api/v1 "
                                       "(atau ketik batal):")
        return
    if cmd == "url":
        label, url = ai_helper.OPENAI_PRESETS[int(parts[1])]
        app_settings.set("OPENAI_BASE_URL", url)
        await safe_edit(query.message, *ai_view(f"✅ Alamat API: {label}.\n\n"))
        await ask_api_key(query.message, context, "openai")
        return

    if cmd == "auth" and parts[1] == "key":
        app_settings.set("CLAUDE_AUTH", "key")
        await safe_edit(query.message, *ai_view())
        if not ai_helper.setting("ANTHROPIC_API_KEY"):
            await ask_api_key(query.message, context, "claude")
        return
    if cmd == "auth" and parts[1] == "login":
        app_settings.set("CLAUDE_AUTH", "login")
        await safe_edit(query.message, "⏳ Mengecek login Claude di VPS...")
        try:
            models = await asyncio.to_thread(ai_helper.list_models, "claude")
        except Exception as e:
            log.info("login Claude belum siap: %s", e)
            await safe_edit(query.message, f"❌ {e}\n\n{CLAUDE_LOGIN_STEPS}",
                            kb([("🔄 Cek login", "ai:auth:login")], [("🔑 Pakai API key saja", "ai:auth:key")],
                               [("⬅️ Kembali", "ai")]))
            return
        note = f"✅ Login Claude terdeteksi ({len(models)} model tersedia).\n"
        if models and ai_helper.current_model() not in models:
            note += (f"⚠️ Model sekarang ({ai_helper.current_model()}) tidak ada di daftar akun ini. "
                     f"Pilih lewat 🧠 Pilih model.\n")
        await safe_edit(query.message, *ai_view(note + "\n"))
        return

    if cmd in ("model", "fb", "vis"):
        code = {"model": "m", "fb": "f", "vis": "v"}[cmd]
        await safe_edit(query.message, "⏳ Mengambil daftar model dari provider...")
        try:
            models = await asyncio.to_thread(ai_helper.list_models, ai_helper.provider())
        except Exception as e:
            log.info("gagal mengambil daftar model: %s", e)
            await safe_edit(query.message, f"Gagal mengambil daftar model: {e}",
                            kb([("✏️ Ketik nama model", f"ai:type:{code}")], [("⬅️ Kembali", "ai")]))
            return
        total = len(models)
        models = models[:MAX_MODEL_BUTTONS]
        context.user_data["ai_models"] = models
        current = ai_helper.setting(MODEL_TARGETS[code]())
        rows = [[(f"{'✅ ' if m == current else ''}{m}", f"ai:set:{code}:{i}")] for i, m in enumerate(models)]
        if code == "f":
            rows.append([("🚫 Tanpa cadangan", "ai:set:f:none")])
        rows += [[("✏️ Ketik nama model", f"ai:type:{code}")], [("⬅️ Kembali", "ai")]]
        if not models:
            text = "Provider tidak mengembalikan model apa pun. Ketik nama model secara manual."
        elif total > len(models):
            text = (f"Pilih {MODEL_TARGET_LABEL[code]} ({total} model tersedia, {len(models)} pertama ditampilkan; "
                    f"model gratis di atas). Model lain bisa diketik lewat ✏️ Ketik nama model:")
        else:
            text = f"Pilih {MODEL_TARGET_LABEL[code]} ({total} model tersedia untuk akun kamu):"
        await safe_edit(query.message, text, kb(*rows))
        return

    if cmd == "set":
        code, choice = parts[1], parts[2]
        models = context.user_data.get("ai_models") or []
        if choice != "none" and int(choice) >= len(models):
            await safe_edit(query.message, *ai_view("Daftar model sudah kedaluwarsa, pilih lagi.\n\n"))
            return
        value = "" if choice == "none" else models[int(choice)]
        app_settings.set(MODEL_TARGETS[code](), value)
        await safe_edit(query.message, *ai_view(
            f"✅ {MODEL_TARGET_LABEL[code].capitalize()}: {value or 'tidak ada'}. Tekan 🧪 Tes AI untuk mencoba.\n\n"))
        return

    if cmd == "type":
        context.user_data["ai_form"] = {"step": "model", "target": parts[1]}
        hint = " Untuk beberapa model cadangan, pisahkan dengan koma." if parts[1] == "f" else ""
        await query.message.reply_text(f"Ketik nama {MODEL_TARGET_LABEL[parts[1]]}.{hint} (atau ketik batal):")
        return

    if cmd == "key":
        await ask_api_key(query.message, context, ai_helper.provider())
        return
    if cmd == "nokey":
        app_settings.set("OPENAI_API_KEY", "")
        context.user_data["ai_form"] = None
        await safe_edit(query.message, *ai_view("✅ Tanpa API key. Sekarang pilih model lewat 🧠 Pilih model.\n\n"))
        return

    if cmd == "test":
        await safe_edit(query.message, f"⏳ Mengetes {ai_helper.provider()} · {ai_helper.current_model()}...")
        started = time.monotonic()
        try:
            answer = await asyncio.to_thread(ai_helper.ping)
            note = f"✅ AI menjawab dalam {time.monotonic() - started:.1f} detik: \"{answer[:40]}\"\n\n"
        except Exception as e:
            log.exception("tes AI gagal (%s · %s)", ai_helper.provider(), ai_helper.current_model())
            note = f"❌ Tes gagal: {e}\n\n"
        await safe_edit(query.message, *ai_view(note))
        return

    await safe_edit(query.message, *ai_view())


async def handle_ai_form(message, context, uid: int, text: str):
    """Isian alamat API / API key / nama model yang diketik manual."""
    form = context.user_data["ai_form"]
    if text.lower() == "batal":
        context.user_data["ai_form"] = None
        await message.reply_text("Dibatalkan.")
        return

    if form["step"] == "url":
        url = text.strip().rstrip("/")
        if not URL_RE.fullmatch(url):
            await message.reply_text("Alamat harus diawali http:// atau https://. Coba lagi (atau ketik batal).")
            return
        app_settings.set("OPENAI_BASE_URL", url)
        await ask_api_key(message, context, "openai")
        return

    if form["step"] == "model":
        context.user_data["ai_form"] = None
        app_settings.set(MODEL_TARGETS[form["target"]](), text.strip())
        reply, markup = ai_view(f"✅ {MODEL_TARGET_LABEL[form['target']].capitalize()}: {text.strip()}. "
                                f"Tekan 🧪 Tes AI untuk mencoba.\n\n")
        await message.reply_text(reply, reply_markup=markup)
        return

    # langkah API key: hapus pesannya dari chat, lalu kirim pesan baru (bukan reply)
    try:
        await message.delete()
    except BadRequest:
        log.warning("pesan berisi API key tidak bisa dihapus")
    provider, key = form["provider"], text.strip()
    msg = await context.bot.send_message(message.chat_id, "⏳ Memeriksa API key...")
    note = ""
    try:
        models = await asyncio.to_thread(ai_helper.list_models, provider, key)
    except ai_helper.NoModelList as e:
        models, note = [], f"ℹ️ Key disimpan tanpa diperiksa: {e}\n"
    except Exception as e:
        log.info("API key %s ditolak: %s", provider, e)
        await msg.edit_text(f"❌ {e}\nKirim API key lagi, atau ketik batal.")
        return
    app_settings.set(ai_helper.PROVIDERS[provider]["key"], key)
    if provider == "claude":
        app_settings.set("CLAUDE_AUTH", "key")
    context.user_data["ai_form"] = None
    note = (note or f"✅ API key disimpan ({len(models)} model tersedia).\n")
    if ai_helper.provider() == provider and models and ai_helper.current_model() not in models:
        note += (f"⚠️ Model sekarang ({ai_helper.current_model() or 'belum dipilih'}) tidak ada di daftar. "
                 f"Pilih lewat 🧠 Pilih model.\n")
    reply, markup = ai_view(note + "\n")
    await msg.edit_text(reply, reply_markup=markup)


def font_view(data: dict, note: str = ""):
    choice = data.get("font_cv", "auto")
    raw = data.get("font_asli")
    auto_family = cv_fonts.resolve(raw)
    text = (f"{note}🔤 Font CV yang disesuaikan\n\n"
            f"Font CV asli kamu: {cv_fonts.clean_name(raw) if raw else 'tidak terdeteksi'}\n"
            f"Dipakai sekarang: {cv_fonts.label(cv_font_for(data))}\n\n"
            "Font Microsoft (Calibri, Arial, Times New Roman, Cambria, Georgia) diganti padanannya yang "
            "ukurannya identik, karena font Microsoft tidak boleh disebarkan bebas.")
    rows = [[(f"{'✅ ' if choice == 'auto' else ''}📄 Sama seperti CV asli ({cv_fonts.label(auto_family)})",
              "font:auto")]]
    rows += [[(f"{'✅ ' if choice == fam else ''}{label}", f"font:{i}")] for i, (fam, label) in enumerate(cv_fonts.CHOICES)]
    rows += [[("✏️ Font lain dari Google Fonts", "font:type")], [("⬅️ Pengaturan", "set:back")]]
    return text, kb(*rows)


async def btn_font(query, context, uid, data, arg):
    """font, font:auto, font:<i>, font:type"""
    if arg == "type":
        context.user_data["font_form"] = True
        await query.message.reply_text("Ketik nama font persis seperti di fonts.google.com, contoh: Raleway, Inter, "
                                       "Nunito, Source Sans 3 (atau ketik batal):")
        return
    if arg == "auto" or arg.isdigit():
        value = "auto" if arg == "auto" else cv_fonts.CHOICES[int(arg)][0]
        data = update_user(uid, lambda d: d.__setitem__("font_cv", value) or d)
        text, markup = font_view(data, "✅ Font disimpan, berlaku untuk CV berikutnya.\n\n")
    else:
        text, markup = font_view(data)
    await safe_edit(query.message, text, markup)


async def handle_font_form(message, context, uid: int, text: str):
    if text.lower() == "batal":
        context.user_data["font_form"] = False
        await message.reply_text("Dibatalkan.")
        return
    family = " ".join(text.split())
    msg = await message.reply_text(f"⏳ Mengambil font {family} dari Google Fonts...")
    _, _, ok = await asyncio.to_thread(cv_fonts.ensure, family)
    if not ok:
        await msg.edit_text(f"Font \"{family}\" tidak ditemukan di Google Fonts (atau VPS tidak bisa mengaksesnya). "
                            "Cek ejaannya di fonts.google.com lalu kirim lagi, atau ketik batal.")
        return
    context.user_data["font_form"] = False
    data = update_user(uid, lambda d: d.__setitem__("font_cv", family) or d)
    reply, markup = font_view(data, f"✅ Font {family} disimpan, berlaku untuk CV berikutnya.\n\n")
    await msg.edit_text(reply, reply_markup=markup)


# ---- 📄 CV Saya: ubah CV asli
CV_STALE = "Data CV sudah berubah, buka lagi dari 📄 CV Saya."
CV_FORM_HINT = "\n\nKetik batal untuk membatalkan."


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


def cv_overview(data: dict, note: str = ""):
    prof = data["profile"]
    counts = {sec: len(pe.entries(dict(prof), sec)) for sec in pe.ENTRIES}
    counts.update({sec: len(pe.list_items(dict(prof), sec)) for sec in pe.LISTS})
    contact = " · ".join(x for x in (prof.get("email"), prof.get("telepon"), prof.get("lokasi")) if x) or "-"
    summary = prof.get("ringkasan") or "-"
    text = (f"{note}📄 CV Saya\nIni CV asli yang jadi dasar semua penyesuaian CV dan email lamaran.\n\n"
            f"👤 {prof.get('nama') or '-'}\n{contact}\n\n"
            f"📝 {summary[:400]}{'…' if len(summary) > 400 else ''}")
    if data.get("profile_edited"):
        text += "\n\n✏️ Sudah diubah lewat bot: lampiran \"CV asli\" dibuat ulang dari data ini."
    return text, kb(
        [("👤 Data diri", "cv:me"), ("📝 Ringkasan", "cv:f:ringkasan")],
        [(f"💼 Pengalaman ({counts['pengalaman']})", "cv:e:pengalaman"), (f"🧩 Proyek ({counts['proyek']})", "cv:e:proyek")],
        [(f"🛠️ Skill ({counts['skill']})", "cv:l:skill"), (f"🎓 Pendidikan ({counts['pendidikan']})", "cv:l:pendidikan")],
        [(f"📜 Sertifikasi ({counts['sertifikasi']})", "cv:l:sertifikasi"), (f"🌐 Bahasa ({counts['bahasa']})", "cv:l:bahasa")],
        [("✨ Perbaiki poin dengan PAR", "cv:par")],
        [("📥 Unduh CV (PDF)", "cv:dl")])


def cv_me_view(data: dict, note: str = ""):
    prof = data["profile"]
    lines = [f"{label}: {prof.get(f) or '-'}" for f, label in pe.FIELDS.items() if f != "ringkasan"]
    links = pe.list_items(dict(prof), "tautan")
    return (f"{note}👤 Data diri\n\n" + "\n".join(lines) + f"\nTautan: {', '.join(links) or '-'}",
            kb([("✏️ Nama", "cv:f:nama"), ("✏️ Email", "cv:f:email")],
               [("✏️ Telepon", "cv:f:telepon"), ("✏️ Lokasi", "cv:f:lokasi")],
               [(f"🔗 Tautan ({len(links)})", "cv:l:tautan")], [("⬅️ CV Saya", "cv")]))


def cv_list_view(data: dict, sec: str, note: str = ""):
    items = pe.list_items(dict(data["profile"]), sec)
    back = "cv:me" if sec == "tautan" else "cv"
    lines = [f"{i + 1}. {item}" for i, item in enumerate(items)] or ["(belum ada)"]
    rows = [[(f"{i + 1}. {item[:30]}", f"cv:li:{sec}:{i}")] for i, item in enumerate(items[:40])]
    return (f"{note}{pe.LISTS[sec]}\n\n" + "\n".join(lines)[:3500] + "\n\nPilih item untuk diubah/dihapus.",
            kb(*rows, [("➕ Tambah", f"cv:la:{sec}")], [("⬅️ Kembali", back)]))


def cv_item_view(data: dict, sec: str, i: int):
    item = pe.list_items(dict(data["profile"]), sec)[i]
    return (f"{pe.LISTS[sec]} #{i + 1}\n\n{item}",
            kb([("✏️ Ubah", f"cv:le:{sec}:{i}"), ("🗑️ Hapus", f"cv:ld:{sec}:{i}"), ("⬆️ Naikkan", f"cv:lu:{sec}:{i}")],
               [("⬅️ Kembali", f"cv:l:{sec}")]))


def cv_entries_view(data: dict, sec: str, note: str = ""):
    items = pe.entries(dict(data["profile"]), sec)
    lines = [f"{i + 1}. {pe.entry_title(sec, e)} · {len(e['poin'])} poin" for i, e in enumerate(items)] or ["(belum ada)"]
    rows = [[(f"{i + 1}. {pe.entry_title(sec, e)[:40]}", f"cv:ei:{sec}:{i}")] for i, e in enumerate(items[:30])]
    return (f"{note}{pe.ENTRIES[sec]['label']}\n\n" + "\n".join(lines)[:3500],
            kb(*rows, [("➕ Tambah", f"cv:ea:{sec}")], [("⬅️ CV Saya", "cv")]))


def cv_entry_view(data: dict, sec: str, i: int, note: str = ""):
    entry = pe.entries(dict(data["profile"]), sec)[i]
    fields = pe.ENTRIES[sec]["fields"]
    lines = [f"{label}: {entry.get(f) or '-'}" for f, label in fields.items()]
    points = [f"{j + 1}. {p}" for j, p in enumerate(entry["poin"])] or ["(belum ada poin)"]
    rows = [[(f"✏️ {label}", f"cv:ef:{sec}:{i}:{f}") for f, label in fields.items()]]
    numbers = [(f"• {j + 1}", f"cv:p:{sec}:{i}:{j}") for j in range(min(len(entry["poin"]), 20))]
    rows += [numbers[k:k + 5] for k in range(0, len(numbers), 5)]
    rows += [[("➕ Tambah poin", f"cv:pa:{sec}:{i}"), ("⬆️ Naikkan", f"cv:eu:{sec}:{i}")],
             [("🗑️ Hapus", f"cv:ed:{sec}:{i}"), ("⬅️ Kembali", f"cv:e:{sec}")]]
    text = (f"{note}{pe.ENTRIES[sec]['label']} #{i + 1}\n\n" + "\n".join(lines) + "\n\nPoin:\n" + "\n".join(points))
    return text[:4000] + ("\n\nTekan nomor poin untuk mengubah, menghapus, atau mengurutkan." if entry["poin"] else ""), kb(*rows)


def cv_point_view(data: dict, sec: str, i: int, j: int):
    point = pe.entries(dict(data["profile"]), sec)[i]["poin"][j]
    return (f"Poin {j + 1}\n\n{point}",
            kb([("✏️ Ubah", f"cv:pe:{sec}:{i}:{j}"), ("🗑️ Hapus", f"cv:pd:{sec}:{i}:{j}"), ("⬆️ Naikkan", f"cv:pu:{sec}:{i}:{j}")],
               [("⬅️ Kembali", f"cv:ei:{sec}:{i}")]))


def cv_form_prompt(form: dict, profile: dict) -> str:
    kind, sec = form["kind"], form.get("sec")
    if kind == "field":
        current = profile.get(form["field"]) or "-"
        clear = "" if form["field"] == "nama" else " Ketik - untuk mengosongkan."
        return f"Ketik {pe.FIELDS[form['field']].lower()} yang baru.{clear}\n\nSekarang:\n{current}{CV_FORM_HINT}"
    if kind == "list_add":
        sep = "pisahkan dengan koma atau baris baru" if sec == "skill" else "satu per baris"
        return f"Ketik {pe.LISTS[sec].split(' ', 1)[1].lower()} yang mau ditambahkan ({sep}).{CV_FORM_HINT}"
    if kind == "list_set":
        return f"Ketik isi baru.\n\nSekarang:\n{pe.list_items(dict(profile), sec)[form['i']]}{CV_FORM_HINT}"
    if kind == "entry_field":
        current = pe.entries(dict(profile), sec)[form["i"]].get(form["field"]) or "-"
        label = pe.ENTRIES[sec]["fields"][form["field"]]
        return f"Ketik {label.lower()} yang baru. Ketik - untuk mengosongkan.\n\nSekarang:\n{current}{CV_FORM_HINT}"
    if kind == "point_add":
        return ("Ketik poin baru, satu per baris. Supaya kuat, tulis dengan pola PAR: masalah, apa yang kamu "
                "lakukan, dan hasilnya (pakai angka kalau ada)." + CV_FORM_HINT)
    if kind == "point_set":
        current = pe.entries(dict(profile), sec)[form["i"]]["poin"][form["j"]]
        return f"Ketik poin yang baru.\n\nSekarang:\n{current}{CV_FORM_HINT}"
    if kind == "entry_add":
        fields = list(pe.ENTRIES[sec]["fields"].items())
        if form["step"] < len(fields):
            optional = " (ketik - kalau tidak ada)" if fields[form["step"]][0] != fields[0][0] else ""
            example = {"posisi": " Contoh: Backend Developer", "perusahaan": " Contoh: PT Maju Jaya",
                       "periode": " Contoh: Jan 2024 - Sekarang", "nama": " Contoh: Aplikasi Kasir UMKM"}
            return (f"{pe.ENTRIES[sec]['label']} baru ({form['step'] + 1}/{len(fields) + 1})\n"
                    f"Ketik {fields[form['step']][1].lower()}{optional}.{example.get(fields[form['step']][0], '')}"
                    f"{CV_FORM_HINT}")
        return (f"{pe.ENTRIES[sec]['label']} baru ({len(fields) + 1}/{len(fields) + 1})\n"
                "Ketik poin-poinnya, satu per baris (pola PAR: masalah, tindakan, hasil). Ketik - kalau mau "
                f"diisi nanti.{CV_FORM_HINT}")
    raise ValueError(kind)


async def btn_cv(query, context, uid, data, arg):
    if not data.get("profile"):
        await query.message.reply_text("Belum ada CV. Kirim dulu file PDF/DOCX kamu.")
        return
    parts = arg.split(":") if arg else []
    cmd, sec = (parts + ["", ""])[:2]
    nums = [int(x) for x in parts[2:] if x.isdigit()]
    try:
        await _cv_action(query, context, uid, data, cmd, sec, nums, parts)
    except (IndexError, KeyError, ValueError):
        log.info("aksi CV kedaluwarsa: %s", arg)
        text, markup = cv_overview(load_user(uid), f"{CV_STALE}\n\n")
        await safe_edit(query.message, text, markup)


async def _cv_action(query, context, uid, data, cmd, sec, nums, parts):
    show = lambda view: safe_edit(query.message, *view)  # noqa: E731

    def ask(form):
        context.user_data["cv_form"] = form
        return query.message.reply_text(cv_form_prompt(form, data["profile"]))

    if cmd == "":
        return await show(cv_overview(data))
    if cmd == "dl":
        edited = data.get("profile_edited")
        await query.message.reply_text("⏳ Menyiapkan PDF CV...")
        path = await ensure_master_cv(uid)
        if not path or not Path(path).exists():
            return await query.message.reply_text("File CV asli tidak ditemukan. Kirim ulang file CV kamu.")
        with open(path, "rb") as f:
            await query.message.reply_document(f, filename=Path(path).name, caption=(
                "CV asli versi terbaru (dibuat dari data 📄 CV Saya)." if edited else
                "Ini file CV asli yang kamu kirim. Setelah ada yang diubah lewat 📄 CV Saya, PDF-nya dibuat ulang."))
        return
    if cmd == "me":
        return await show(cv_me_view(data))
    if cmd == "par":
        await query.edit_message_reply_markup(None)
        return await start_master_par(query.message, context, uid)
    if cmd == "parok":
        return await save_master_par(query, context, uid)
    if cmd == "parno":
        context.user_data["cv_par_pending"] = None
        return await show(cv_overview(data, "Dibatalkan, CV asli tidak diubah.\n\n"))
    if cmd == "f":
        if sec not in pe.FIELDS:
            raise KeyError(sec)
        return await ask({"kind": "field", "field": sec})

    if cmd == "l":
        return await show(cv_list_view(data, sec))
    if cmd == "li":
        return await show(cv_item_view(data, sec, nums[0]))
    if cmd == "la":
        pe.LISTS[sec]
        return await ask({"kind": "list_add", "sec": sec})
    if cmd == "le":
        pe.list_items(dict(data["profile"]), sec)[nums[0]]
        return await ask({"kind": "list_set", "sec": sec, "i": nums[0]})
    if cmd == "ld":
        removed = edit_profile(uid, lambda prof: pe.list_delete(prof, sec, nums[0]))
        return await show(cv_list_view(load_user(uid), sec, f"🗑️ Dihapus: {removed}\n\n"))
    if cmd == "lu":
        edit_profile(uid, lambda prof: pe.list_move_up(prof, sec, nums[0]))
        return await show(cv_list_view(load_user(uid), sec))

    if cmd == "e":
        return await show(cv_entries_view(data, sec))
    if cmd == "ei":
        return await show(cv_entry_view(data, sec, nums[0]))
    if cmd == "ea":
        pe.ENTRIES[sec]
        return await ask({"kind": "entry_add", "sec": sec, "step": 0, "entry": {}})
    if cmd == "ef":
        field = parts[3]
        pe.ENTRIES[sec]["fields"][field]
        pe.entries(dict(data["profile"]), sec)[nums[0]]
        return await ask({"kind": "entry_field", "sec": sec, "i": nums[0], "field": field})
    if cmd == "eu":
        edit_profile(uid, lambda prof: pe.entry_move_up(prof, sec, nums[0]))
        return await show(cv_entries_view(load_user(uid), sec))
    if cmd == "ed":
        entry = pe.entries(dict(data["profile"]), sec)[nums[0]]
        return await safe_edit(query.message, f"Hapus {pe.entry_title(sec, entry)} beserta semua poinnya?",
                               kb([("🗑️ Ya, hapus", f"cv:edok:{sec}:{nums[0]}"), ("❌ Batal", f"cv:ei:{sec}:{nums[0]}")]))
    if cmd == "edok":
        removed = edit_profile(uid, lambda prof: pe.entry_delete(prof, sec, nums[0]))
        return await show(cv_entries_view(load_user(uid), sec, f"🗑️ Dihapus: {pe.entry_title(sec, removed)}\n\n"))

    if cmd == "pa":
        pe.entries(dict(data["profile"]), sec)[nums[0]]
        return await ask({"kind": "point_add", "sec": sec, "i": nums[0]})
    if cmd == "p":
        return await show(cv_point_view(data, sec, nums[0], nums[1]))
    if cmd == "pe":
        pe.entries(dict(data["profile"]), sec)[nums[0]]["poin"][nums[1]]
        return await ask({"kind": "point_set", "sec": sec, "i": nums[0], "j": nums[1]})
    if cmd == "pd":
        edit_profile(uid, lambda prof: pe.point_delete(prof, sec, nums[0], nums[1]))
        return await show(cv_entry_view(load_user(uid), sec, nums[0], "🗑️ Poin dihapus.\n\n"))
    if cmd == "pu":
        edit_profile(uid, lambda prof: pe.point_move_up(prof, sec, nums[0], nums[1]))
        return await show(cv_entry_view(load_user(uid), sec, nums[0]))
    raise ValueError(cmd)


# ---- ✨ Perbaiki poin dengan PAR (CV asli)
async def start_master_par(message, context, uid: int):
    data = load_user(uid)
    profile = data["profile"]
    if not any(e["poin"] for sec in pe.ENTRIES for e in pe.entries(dict(profile), sec)):
        await message.reply_text("Belum ada poin pengalaman atau proyek untuk diperbaiki.",
                                 reply_markup=kb([("⬅️ CV Saya", "cv")]))
        return
    msg = await message.reply_text("⏳ AI sedang memperbaiki semua poin pengalaman & proyek dengan metode PAR...")
    try:
        result = await asyncio.to_thread(ai_helper.improve_cv_par, profile, data.get("hasil_poin") or [])
    except Exception as e:
        log.exception("perbaikan PAR gagal")
        await msg.edit_text(f"Gagal memperbaiki poin: {e}", reply_markup=kb([("🔄 Coba lagi", "cv:par")],
                                                                          [("⬅️ CV Saya", "cv")]))
        return
    questions = par_questions(data, profile, result)
    if questions:
        context.user_data["par"] = {"mode": "master", "questions": questions, "i": 0, "answers": [], "result": result}
        await msg.delete()
        await ask_par_question(message, context)
        return
    await show_master_preview(msg, message, context, uid, result)


async def finish_master_par(message, context, uid: int, par: dict):
    answers, result = par["answers"], par["result"]
    if not answers:
        msg = await message.reply_text("⏳ Menyiapkan pratinjau...")
        await show_master_preview(msg, message, context, uid, result)
        return
    data = save_facts(uid, answers)
    msg = await message.reply_text(f"⏳ Menyusun ulang poin dengan {len(answers)} hasil dari kamu...")
    try:
        result = await asyncio.to_thread(ai_helper.improve_cv_par, data["profile"], data["hasil_poin"], False)
    except Exception:
        log.exception("perbaikan PAR ulang gagal, pakai hasil pertama")
    await show_master_preview(msg, message, context, uid, result)


async def show_master_preview(msg, message, context, uid: int, result: dict):
    """Tampilkan sebelum/sesudah. CV asli baru berubah setelah pengguna menekan Simpan."""
    data = load_user(uid)
    _, changes, rejected = pe.merge_points(data["profile"], result, data.get("hasil_poin") or [])
    if not changes:
        note = (f"\n\n⚠️ {len(rejected)} usulan AI ditolak karena memuat angka yang tidak ada di CV maupun jawaban kamu."
                if rejected else "")
        await msg.edit_text(f"✅ Poin CV kamu sudah rapi, tidak ada yang perlu diubah.{note}",
                            reply_markup=kb([("⬅️ CV Saya", "cv")]))
        return
    context.user_data["cv_par_pending"] = {"changes": changes, "snapshot": pe.points_snapshot(data["profile"])}

    summary = "\n".join(f"• {c}" for c in result.get("perubahan") or [])
    blocks = [f"✨ Pratinjau perbaikan PAR ({len(changes)} entri berubah)" + (f"\n\nYang diperbaiki:\n{summary}" if summary else "")]
    for c in changes:
        label = pe.ENTRIES[c["sec"]]["label"].split(" ", 1)[0]
        before = "\n".join(f"  • {p}" for p in c["before"]) or "  (kosong)"
        after = "\n".join(f"  • {p}" for p in c["after"])
        blocks.append(f"{label} {c['title']}\nSebelum:\n{before}\nSesudah:\n{after}")
    footer = "Simpan ke CV asli?"
    if rejected:
        footer = (f"⚠️ {len(rejected)} poin dikembalikan ke aslinya karena AI menulis angka yang tidak ada di CV "
                  f"maupun jawaban kamu.\n\n{footer}")
    blocks.append(footer)

    chunks, current = [], ""
    for block in blocks:
        block = block[:3500]
        if current and len(current) + len(block) + 2 > 3500:
            chunks.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    chunks.append(current)
    buttons = kb([("✅ Simpan ke CV asli", "cv:parok"), ("❌ Batal", "cv:parno")])
    if len(chunks) == 1:
        await msg.edit_text(chunks[0], reply_markup=buttons)
        return
    await msg.edit_text(chunks[0])
    for chunk in chunks[1:-1]:
        await message.reply_text(chunk)
    await message.reply_text(chunks[-1], reply_markup=buttons)


async def save_master_par(query, context, uid: int):
    pending = context.user_data.get("cv_par_pending")
    await query.edit_message_reply_markup(None)
    if not pending:
        await query.message.reply_text("Pratinjau ini sudah tidak berlaku. Jalankan ✨ Perbaiki poin dengan PAR lagi.")
        return
    if pe.points_snapshot(load_user(uid)["profile"]) != pending["snapshot"]:
        context.user_data["cv_par_pending"] = None
        await query.message.reply_text("CV asli sudah diubah sejak pratinjau dibuat, jadi tidak disimpan supaya "
                                       "perubahanmu tidak tertimpa. Jalankan ✨ Perbaiki poin dengan PAR lagi.")
        return

    def apply(prof):
        for c in pending["changes"]:
            pe.entries(prof, c["sec"])[c["i"]]["poin"] = c["after"]

    edit_profile(uid, apply)
    context.user_data["cv_par_pending"] = None
    text, markup = cv_overview(load_user(uid), f"✅ {len(pending['changes'])} entri diperbarui dengan metode PAR.\n\n")
    await query.message.reply_text(text, reply_markup=markup)


async def handle_cv_form(message, context, uid: int, text: str):
    """Isian teks untuk 📄 CV Saya. Setelah tersimpan, tampilkan bagian yang baru diubah."""
    form = context.user_data["cv_form"]
    if text.lower() == "batal":
        context.user_data["cv_form"] = None
        await message.reply_text("Dibatalkan.")
        return
    value = "" if text.strip() == "-" else text.strip()
    kind, sec = form["kind"], form.get("sec")

    async def done(view):
        context.user_data["cv_form"] = None
        reply, markup = view
        await message.reply_text(reply, reply_markup=markup)

    try:
        if kind == "field":
            if form["field"] == "nama" and not value:
                await message.reply_text("Nama tidak boleh kosong. Ketik nama kamu, atau ketik batal.")
                return
            edit_profile(uid, lambda prof: pe.set_field(prof, form["field"], value))
            data = load_user(uid)
            view = cv_overview(data, "✅ Tersimpan.\n\n") if form["field"] == "ringkasan" else cv_me_view(data, "✅ Tersimpan.\n\n")
            return await done(view)
        if kind == "list_add":
            raw = value.replace(",", "\n") if sec == "skill" else value
            added = edit_profile(uid, lambda prof: pe.list_add(prof, sec, raw.splitlines()))
            return await done(cv_list_view(load_user(uid), sec, f"✅ {added} ditambahkan.\n\n"))
        if kind == "list_set":
            if not value:
                await message.reply_text("Isinya tidak boleh kosong. Untuk menghapus, pakai tombol 🗑️ Hapus.")
                return
            edit_profile(uid, lambda prof: pe.list_set(prof, sec, form["i"], value))
            return await done(cv_list_view(load_user(uid), sec, "✅ Tersimpan.\n\n"))
        if kind == "entry_field":
            edit_profile(uid, lambda prof: pe.entry_set_field(prof, sec, form["i"], form["field"], value))
            return await done(cv_entry_view(load_user(uid), sec, form["i"], "✅ Tersimpan.\n\n"))
        if kind == "point_add":
            added = edit_profile(uid, lambda prof: pe.point_add(prof, sec, form["i"], value.splitlines()))
            return await done(cv_entry_view(load_user(uid), sec, form["i"], f"✅ {added} poin ditambahkan.\n\n"))
        if kind == "point_set":
            if not value:
                await message.reply_text("Poin tidak boleh kosong. Untuk menghapus, pakai tombol 🗑️ Hapus.")
                return
            edit_profile(uid, lambda prof: pe.point_set(prof, sec, form["i"], form["j"], value))
            return await done(cv_entry_view(load_user(uid), sec, form["i"], "✅ Tersimpan.\n\n"))
        if kind == "entry_add":
            fields = list(pe.ENTRIES[sec]["fields"])
            if form["step"] < len(fields):
                if form["step"] == 0 and not value:
                    await message.reply_text("Bagian ini wajib diisi. Ketik lagi, atau ketik batal.")
                    return
                form["entry"][fields[form["step"]]] = value
                form["step"] += 1
                await message.reply_text(cv_form_prompt(form, load_user(uid)["profile"]))
                return
            form["entry"]["poin"] = value.splitlines()
            i = edit_profile(uid, lambda prof: pe.entry_add(prof, sec, form["entry"]))
            return await done(cv_entry_view(load_user(uid), sec, i, "✅ Ditambahkan di urutan paling atas.\n\n"))
    except (IndexError, KeyError):
        context.user_data["cv_form"] = None
        await message.reply_text(CV_STALE)


# ---- 🎯 Preferensi loker
def pref_view(data: dict, note: str = ""):
    prefs = data.get("pref") or {}
    mode = prefs.get("mode") or "semua"
    text = (f"{note}🎯 Preferensi loker\n\n"
            f"📍 Lokasi: {', '.join(prefs.get('lokasi') or []) or 'semua'}\n"
            f"🏠 Mode kerja: {rules.MODES[mode]}\n"
            f"💰 Gaji minimal: {rules.rupiah(prefs['gaji_min']) if prefs.get('gaji_min') else '-'}\n"
            f"🚫 Hindari posisi: {', '.join(prefs.get('hindari') or []) or '-'}\n\n"
            "Lowongan yang tidak sesuai disaring dari 🎯 Rekomendasi, dan diberi peringatan kalau kamu kirim manual. "
            "Info yang tidak disebut di lowongan tidak dianggap masalah.")
    modes = [(f"{'✅ ' if key == mode else ''}{label}", f"pref:mode:{key}") for key, label in rules.MODES.items()]
    return text, kb([("📍 Lokasi", "pref:lok"), ("💰 Gaji minimal", "pref:gaji")], modes,
                    [("🚫 Posisi yang dihindari", "pref:hindari")], [("🔄 Reset", "pref:reset"), ("⬅️ Pengaturan", "set:back")])


PREF_PROMPTS = {
    "lok": "Ketik kota yang kamu mau, pisahkan koma (contoh: Malang, Surabaya, Jakarta). Lowongan remote selalu lolos.",
    "gaji": "Ketik gaji minimal per bulan (contoh: 5jt atau 5.000.000).",
    "hindari": "Ketik kata di judul posisi yang mau kamu hindari, pisahkan koma (contoh: sales, telemarketing).",
}


async def btn_pref(query, context, uid, data, arg):
    """pref, pref:lok|gaji|hindari, pref:mode:<x>, pref:reset"""
    parts = arg.split(":") if arg else [""]
    if parts[0] in PREF_PROMPTS:
        context.user_data["pref_form"] = parts[0]
        await query.message.reply_text(PREF_PROMPTS[parts[0]] + "\n\nKetik - untuk mengosongkan, atau batal.")
        return
    note = ""
    if parts[0] == "mode" and parts[1] in rules.MODES:
        data = update_user(uid, lambda d: d.setdefault("pref", {}).__setitem__("mode", parts[1]) or d)
        note = "✅ Tersimpan.\n\n"
    elif parts[0] == "reset":
        data = update_user(uid, lambda d: d.__setitem__("pref", {}) or d)
        note = "✅ Preferensi dikosongkan.\n\n"
    await safe_edit(query.message, *pref_view(data, note))


async def handle_pref_form(message, context, uid: int, text: str):
    field = context.user_data["pref_form"]
    if text.lower() == "batal":
        context.user_data["pref_form"] = None
        await message.reply_text("Dibatalkan.")
        return
    value = text.strip()
    if field == "gaji":
        amount = None if value == "-" else rules.parse_salary(value)
        if value != "-" and not amount:
            await message.reply_text("Tidak terbaca. Contoh: 5jt atau 5.000.000. Coba lagi, atau ketik batal.")
            return
        stored = amount
        key = "gaji_min"
    else:
        stored = [] if value == "-" else [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]
        key = "lokasi" if field == "lok" else "hindari"
    context.user_data["pref_form"] = None
    data = update_user(uid, lambda d: d.setdefault("pref", {}).__setitem__(key, stored) or d)
    reply, markup = pref_view(data, "✅ Tersimpan.\n\n")
    await message.reply_text(reply, reply_markup=markup)


# ---- 🎤 Persiapan interview
def prep_messages(app: dict, prep: dict) -> list[str]:
    blocks = [f"🎤 Persiapan interview: {app.get('posisi')} - {app.get('perusahaan')}\n\n"
              "❓ Pertanyaan yang kemungkinan ditanyakan:"]
    for n, q in enumerate(prep.get("pertanyaan") or [], 1):
        blocks.append(f"{n}. {q.get('q')}" + (f" ({q['tipe']})" if q.get("tipe") else "") +
                      (f"\n   💡 {q['tips']}" if q.get("tips") else ""))
    if prep.get("tanya_balik"):
        blocks.append("🙋 Pertanyaan untuk ditanyakan balik:" + "".join(f"\n• {x}" for x in prep["tanya_balik"]))
    if prep.get("tips"):
        blocks.append("📌 Tips:" + "".join(f"\n• {x}" for x in prep["tips"]))
    chunks, current = [], ""
    for block in blocks:
        if current and len(current) + len(block) + 2 > 3800:
            chunks.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    return chunks + [current]


async def ask_iv_question(message, context):
    iv = context.user_data["iv"]
    q = iv["questions"][iv["i"]]
    await message.reply_text(
        f"🗣️ Latihan {iv['i'] + 1}/{len(iv['questions'])}" + (f" ({q['tipe']})" if q.get("tipe") else "") +
        f"\n\n❓ {q.get('q')}\n\nKetik jawabanmu seperti sedang interview.",
        reply_markup=kb([("⏭️ Lewati", "iv:n"), ("✅ Selesai", "iv:x")]))


async def next_iv_question(message, context):
    iv = context.user_data["iv"]
    iv["i"] += 1
    if iv["i"] < len(iv["questions"]):
        await ask_iv_question(message, context)
    else:
        context.user_data["iv"] = None
        await message.reply_text("✅ Latihan selesai. Semangat untuk interview-nya! 💪")


async def btn_iv(query, context, uid, data, arg):
    """iv:<id> (persiapan), iv:r:<id> (buat ulang), iv:p:<id> (latihan), iv:n (lewati), iv:x (selesai)"""
    parts = arg.split(":")
    if parts[0] in ("n", "x"):
        await query.edit_message_reply_markup(None)
        if not context.user_data.get("iv"):
            await query.message.reply_text("Sesi latihan ini sudah selesai.")
        elif parts[0] == "x":
            context.user_data["iv"] = None
            await query.message.reply_text("✅ Latihan selesai. Semangat untuk interview-nya! 💪")
        else:
            await next_iv_question(query.message, context)
        return
    mode = parts[0] if parts[0] in ("r", "p") else ""
    app_id = int(parts[-1])
    app = tracker.find(data, app_id)
    if not app:
        await query.message.reply_text("Lamaran tidak ditemukan.")
        return
    prep = app.get("interview_prep")
    if not prep or mode == "r":
        msg = await query.message.reply_text("⏳ Menyiapkan pertanyaan interview dari lowongan & CV kamu...")
        try:
            prep = await asyncio.to_thread(ai_helper.interview_prep, data.get("profile") or {}, app,
                                           data.get("skill_konfirmasi") or [], data.get("hasil_poin") or [])
        except Exception as e:
            log.exception("persiapan interview gagal")
            await msg.edit_text(f"Gagal menyiapkan interview: {e}", reply_markup=kb([("🔄 Coba lagi", f"iv:r:{app_id}")]))
            return
        update_user(uid, lambda d: (tracker.find(d, app_id) or {}).__setitem__("interview_prep", prep))
        await msg.delete()
    if mode == "p":
        questions = [q for q in prep.get("pertanyaan") or [] if q.get("q")]
        if not questions:
            await query.message.reply_text("Belum ada pertanyaan untuk latihan.")
            return
        context.user_data["iv"] = {"app_id": app_id, "questions": questions, "i": 0}
        await ask_iv_question(query.message, context)
        return
    chunks = prep_messages(app, prep)
    for chunk in chunks[:-1]:
        await query.message.reply_text(chunk)
    await query.message.reply_text(chunks[-1], reply_markup=kb(
        [("🗣️ Latihan tanya-jawab", f"iv:p:{app_id}")], [("🔄 Buat ulang", f"iv:r:{app_id}")]))


async def handle_iv_answer(message, context, uid: int, text: str):
    iv = context.user_data["iv"]
    q = iv["questions"][iv["i"]]
    data = load_user(uid)
    app = tracker.find(data, iv["app_id"]) or {}
    msg = await message.reply_text("⏳ Menilai jawabanmu...")
    try:
        feedback = await asyncio.to_thread(ai_helper.interview_feedback, q.get("q"), text, data.get("profile") or {},
                                           app.get("posisi") or "")
        await msg.edit_text(f"📝 Umpan balik:\n\n{feedback}"[:4000])
    except Exception as e:
        log.exception("umpan balik interview gagal")
        await msg.edit_text(f"Gagal menilai jawaban: {e}")
    await next_iv_question(message, context)


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
        schedule_queued(context.job_queue, uid, item)
    await query.message.reply_text(f"✅ Data dipulihkan. Data sebelumnya disimpan di {safety.name}.",
                                   reply_markup=main_menu())


async def btn_lamaran_list(query, context, uid, data, arg):
    text, markup = lamaran_list_view(data)
    await safe_edit(query.message, text, markup)


async def btn_app_detail(query, context, uid, data, app_id):
    app = tracker.find(data, int(app_id))
    if not app:
        await query.message.reply_text("Lamaran tidak ditemukan.")
        return
    text, markup = app_detail_view(app)
    await query.message.reply_text(text, reply_markup=markup)


async def btn_status(query, context, uid, data, arg, new_message: bool = False):
    """st:<id>:<status>. new_message dipakai dari pesan berisi banyak lamaran supaya daftarnya tidak tertimpa."""
    app_id, status = arg.split(":", 1)

    def change(d):
        a = tracker.find(d, int(app_id))
        if a:
            tracker.set_status(a, status, TIMEZONE)
        return dict(a) if a else None

    app = update_user(uid, change)
    if not app:
        return
    text, markup = app_detail_view(app)
    if new_message:
        await query.message.reply_text(text, reply_markup=markup)
    else:
        await safe_edit(query.message, text, markup)


async def btn_followup_draft(query, context, uid, data, app_id):
    app = tracker.find(data, int(app_id))
    if not app or not app.get("ke"):
        await query.message.reply_text("Lamaran atau alamat email tujuannya tidak ditemukan.")
        return
    if not data.get("profile"):
        await query.message.reply_text("Profil CV tidak ditemukan. Kirim ulang CV kamu dulu.")
        return
    msg = await query.message.reply_text("⏳ Menulis email follow-up...")
    try:
        text = await asyncio.to_thread(
            ai_helper.write_followup, data["profile"], app, tracker.days_since(app, TIMEZONE))
    except Exception as e:
        log.exception("follow-up gagal")
        await msg.edit_text(f"Gagal membuat follow-up: {e}")
        return
    followup = {"app_id": app["id"], "text": text}
    context.user_data.update({"followup": followup, "draft_kind": "fu"})
    await msg.edit_text(**followup_view(data, followup))


async def btn_followup_send(query, context, uid, data, arg):
    followup = context.user_data.get("followup")
    app = tracker.find(data, followup["app_id"]) if followup else None
    if not app:
        await query.message.reply_text("Draft follow-up sudah tidak ada. Buat ulang lewat /lamaran.")
        return
    account = gmail_accounts.get(app.get("dari"))
    if not account:
        await query.message.reply_text(
            f"Akun Gmail pengirim lamaran ini ({app.get('dari')}) sudah tidak terdaftar. Tambahkan lagi "
            f"lewat ⚙️ Pengaturan > 📧 Akun Gmail." if app.get("dari")
            else GMAIL_MISSING)
        return
    await query.edit_message_reply_markup(None)
    msg = await query.message.reply_text(f"⏳ Mengirim follow-up dari {account.address}...")
    subject = app.get("subjek") or email_subject(app, (data.get("profile") or {}).get("nama") or "")
    try:
        await asyncio.to_thread(mailer.send_application, account, app["ke"], f"Re: {subject}",
                                followup["text"], None, app.get("message_id"))
    except Exception as e:
        log.exception("kirim follow-up gagal")
        await msg.edit_text(f"Gagal mengirim follow-up: {e}")
        return

    def record(d):
        a = tracker.find(d, app["id"])
        if a:
            a.setdefault("followups", []).append(tracker.now(TIMEZONE).isoformat(timespec="minutes"))
            a["reminder_sent"] = True

    update_user(uid, record)
    context.user_data.update({"followup": None, "draft_kind": None})
    await msg.edit_text(f"✅ Follow-up terkirim ke {app['ke']}.")


async def btn_followup_cancel(query, context, uid, data, arg):
    context.user_data.update({"followup": None, "draft_kind": None})
    await query.edit_message_reply_markup(None)
    await query.message.reply_text("Follow-up dibatalkan.")


async def btn_recommendation(query, context, uid, data, rid):
    rec = (context.user_data.get("recs") or {}).get(rid)
    if not rec:
        await query.message.reply_text("Rekomendasi ini sudah kedaluwarsa. Ketik /rekomendasi lagi.")
        return
    msg = await query.message.reply_text(f"⏳ Membuka lowongan {rec.get('posisi')}...")
    job_text = None
    if rec.get("link"):
        try:
            fetched = await asyncio.to_thread(job_fetcher.fetch_job_text, rec["link"])
            if len(fetched) >= 300:
                job_text = fetched
        except Exception:
            log.info("gagal membuka link rekomendasi", exc_info=True)
    if not job_text:
        await msg.edit_text("Halaman lowongannya tidak bisa dibaca bot (biasanya diblokir situsnya). "
                            "Buka lewat tombol 🌐 Buka, lalu copy-paste deskripsi lowongannya ke sini ya.")
        return
    await msg.delete()
    await process_job(query.message, context, uid, job_text, source_from_link(rec.get("link")))


async def btn_retry_job(query, context, uid, data, arg):
    job_text = context.user_data.get("retry_job_text")
    await query.edit_message_reply_markup(None)
    if not job_text:
        await query.message.reply_text("Lowongan ini sudah tidak tersimpan. Kirim ulang lowongannya ya.")
        return
    await process_job(query.message, context, uid, job_text, context.user_data.get("retry_job_source"))


# ---- tombol di bawah ini butuh lowongan yang sedang diproses (context.user_data["job"])
async def btn_tailor(query, context, uid, data, arg):
    await query.edit_message_reply_markup(None)
    await tailor_flow(query.message, context, uid)


async def btn_original_cv(query, context, uid, data, arg):
    job = context.user_data["job"]
    job["skor"] = job.get("skor_awal", job.get("skor"))
    context.user_data.update({"active_profile": data.get("profile"), "tailored_cv": None, "tailor_pending": None,
                              "gap": None})
    await query.edit_message_reply_markup(None)
    await query.message.reply_text("Oke, pakai CV asli.", reply_markup=kb([("✍️ Buat Email Lamaran", "cl")]))


async def btn_cover_letter(query, context, uid, data, arg):
    profile = context.user_data.get("active_profile") or data.get("profile")
    style = data.get("gaya", "formal")
    msg = await query.message.reply_text(f"⏳ Menulis email lamaran ({GAYA_LABEL.get(style)})...")
    try:
        cl = await asyncio.to_thread(
            ai_helper.write_cover_letter, profile, context.user_data["job"], context.user_data["job_text"], style)
    except Exception as e:
        log.exception("email lamaran gagal")
        await msg.edit_text(f"Gagal membuat email lamaran: {e}")
        return
    context.user_data["cover_letter"] = cl
    context.user_data["draft_kind"] = "cl"
    await msg.edit_text(cl, reply_markup=cover_letter_buttons())
    await query.message.reply_text("Mau diubah? Pakai /revisi <instruksi>, atau /gaya untuk ganti gaya email.")


async def btn_send(query, context, uid, data, arg):
    if not context.user_data.get("cover_letter"):
        await query.message.reply_text("Buat email lamaran dulu.")
        return
    if not gmail_configured():
        await query.message.reply_text(GMAIL_MISSING)
        return
    job = context.user_data["job"]
    if not context.user_data.get("tailored_cv"):
        await ensure_master_cv(uid)
    if job.get("email_hr"):
        context.user_data["recipient"] = job["email_hr"]
        await show_send_confirmation(query.message, context, uid)
    else:
        context.user_data["awaiting_email"] = True
        await query.message.reply_text("Email HR tidak ditemukan di lowongan. Ketik alamat email tujuannya:")


async def send_and_record(uid: int, account, recipient: str, subject: str, body: str, attach: str | None,
                          info: dict) -> dict:
    """Kirim email lamaran lalu catat di 📂 Lamaran. Melempar error kalau gagal kirim."""
    message_id = await asyncio.to_thread(mailer.send_application, account, recipient, subject, body, attach)
    full = {**info, "ke": recipient, "dari": account.address, "subjek": subject, "message_id": message_id}
    return update_user(uid, lambda d: tracker.add_application(d, full, TIMEZONE))


def sent_message(app: dict) -> str:
    return (f"✅ Lamaran #{app['id']} terkirim ke {app['ke']} dari {app['dari']}. Semoga lolos! 🍀\n\n"
            f"Balasan HR akan dicek otomatis, dan kalau {FOLLOWUP_DAYS} hari belum ada kabar "
            f"aku ingatkan untuk follow-up. Lihat semua lamaran di /lamaran.")


async def btn_confirm_send(query, context, uid, data, arg):
    """confirm:<indeks akun>[:now|:later]. now = abaikan jadwal & batas harian, later = jadwalkan."""
    recipient = context.user_data.get("recipient")
    if not recipient or not context.user_data.get("cover_letter"):
        await query.message.reply_text("Alamat tujuan atau email lamarannya belum ada.")
        return
    parts = (arg or "0").split(":")
    force = parts[1] if len(parts) > 1 else ""
    accounts = gmail_accounts.load()
    idx = int(parts[0] or 0)
    if idx >= len(accounts):
        await query.message.reply_text("Daftar akun Gmail sudah berubah. Tekan 📧 Kirim via Gmail lagi.")
        return
    account = accounts[idx]
    job = context.user_data["job"]
    await query.edit_message_reply_markup(None)
    subject = email_subject(job, (data.get("profile") or {}).get("nama") or "")
    info = {
        "posisi": job.get("posisi"), "perusahaan": job.get("perusahaan"),
        "skor": job.get("skor"), "bahasa": job.get("bahasa_lowongan"),
        "cv_disesuaikan": bool(context.user_data.get("tailored_cv")), "gaya": data.get("gaya", "formal"),
        "job_text": (context.user_data.get("job_text") or "")[:4000],
    }
    now = tracker.now(TIMEZONE)
    count = rules.sent_on(data.get("lamaran") or [], account.address, now.date())
    if not force and count >= MAX_SEND_PER_DAY:
        await query.message.reply_text(
            f"⛔ {account.address} sudah mengirim {count} lamaran hari ini (batas {MAX_SEND_PER_DAY}). Terlalu banyak "
            "email per hari bisa membuat akun Gmail kamu ditandai spam.",
            reply_markup=kb([(f"🕗 Jadwalkan {rules.when_label(rules.next_work_time(now), now)}", f"confirm:{idx}:later")],
                            [("📤 Tetap kirim sekarang", f"confirm:{idx}:now"), ("❌ Batal", "cancel")]))
        return
    if force == "later" or (force != "now" and data.get("jadwal_kirim", True) and not rules.in_work_hours(now)):
        when = rules.next_work_time(now)
        item = queue_add(uid, when, account.address, recipient, subject, context.user_data["cover_letter"],
                         attachment_path(context, uid), info)
        schedule_queued(context.job_queue, uid, item)
        context.user_data["cover_letter"] = None
        await query.message.reply_text(
            f"🕗 Lamaran ke {recipient} dijadwalkan terkirim {rules.when_label(when, now)} dari {account.address}, "
            "di jam kerja supaya lebih mungkin dibaca HR.",
            reply_markup=kb([("📤 Kirim sekarang saja", f"q:now:{item['id']}"), ("❌ Batalkan", f"q:del:{item['id']}")]))
        return

    msg = await query.message.reply_text(f"⏳ Mengirim email dari {account.address}...")
    try:
        app = await send_and_record(uid, account, recipient, subject, context.user_data["cover_letter"],
                                    attachment_path(context, uid), info)
    except Exception as e:
        log.exception("kirim email gagal")
        where = "file .env" if account.from_env else "⚙️ Pengaturan > 📧 Akun Gmail"
        await msg.edit_text(f"Gagal mengirim: {e}\nCek app password {account.address} di {where}.")
        return
    context.user_data["cover_letter"] = None
    await msg.edit_text(sent_message(app))


# ---- 🕗 antrean kirim (jam kerja / batas harian)
def queue_add(uid: int, when, account: str, recipient: str, subject: str, body: str, attach: str | None,
              info: dict) -> dict:
    def apply(d):
        d["next_queue_id"] = d.get("next_queue_id", 0) + 1
        item = {"id": d["next_queue_id"], "waktu": when.isoformat(timespec="minutes"), "akun": account,
                "ke": recipient, "subjek": subject, "isi": body, "lampiran": attach, "info": info}
        d.setdefault("antrian", []).append(item)
        return item
    return update_user(uid, apply)


def _queue_job_name(uid: int, qid: int) -> str:
    return f"antrian_{uid}_{qid}"


def schedule_queued(job_queue, uid: int, item: dict) -> None:
    if job_queue is None:
        log.warning("job queue tidak aktif, lamaran terjadwal #%s hanya bisa dikirim manual", item["id"])
        return
    when = dt.datetime.fromisoformat(item["waktu"])
    if when.tzinfo is None:
        when = when.replace(tzinfo=ZoneInfo(TIMEZONE))
    now = tracker.now(TIMEZONE)
    if when <= now:
        when = now + dt.timedelta(seconds=30)  # terlewat (misalnya bot sempat mati): kirim sebentar lagi
    job_queue.run_once(send_queued_job, when=when, data={"uid": uid, "id": item["id"]},
                       name=_queue_job_name(uid, item["id"]), chat_id=uid, user_id=uid)


def unschedule_queued(job_queue, uid: int, qid: int) -> None:
    if job_queue is not None:
        for job in job_queue.get_jobs_by_name(_queue_job_name(uid, qid)):
            job.schedule_removal()


async def send_queued_job(context: ContextTypes.DEFAULT_TYPE):
    await deliver_queued(context.bot, context.job.data["uid"], context.job.data["id"])


async def deliver_queued(bot, uid: int, qid: int) -> None:
    """Kirim satu lamaran dari antrean. Kalau gagal, tetap di antrean dan pengguna diberi tahu."""
    item = next((q for q in load_user(uid).get("antrian") or [] if q["id"] == qid), None)
    if not item:
        return
    account = gmail_accounts.get(item["akun"])
    if not account:
        await bot.send_message(uid, f"❌ Lamaran terjadwal ke {item['ke']} gagal: akun {item['akun']} sudah tidak "
                                    "terdaftar. Lihat 📂 Lamaran > 🕗 Antrean kirim.")
        return
    try:
        app = await send_and_record(uid, account, item["ke"], item["subjek"], item["isi"], item["lampiran"], item["info"])
    except Exception as e:
        log.exception("kirim lamaran terjadwal gagal")
        await bot.send_message(uid, f"❌ Lamaran terjadwal ke {item['ke']} gagal dikirim: {e}\n"
                                    "Masih ada di 📂 Lamaran > 🕗 Antrean kirim, bisa dicoba kirim lagi.")
        return
    update_user(uid, lambda d: d.__setitem__("antrian", [q for q in d.get("antrian") or [] if q["id"] != qid]))
    await bot.send_message(uid, "🕗 " + sent_message(app))


def queue_view(data: dict, note: str = ""):
    queue = data.get("antrian") or []
    if not queue:
        return f"{note}Tidak ada lamaran terjadwal.", kb([("⬅️ Daftar lamaran", "lamlist")])
    now = tracker.now(TIMEZONE)
    lines, rows = [], []
    for q in queue:
        when = dt.datetime.fromisoformat(q["waktu"])
        lines.append(f"• {q['info'].get('posisi')} - {q['info'].get('perusahaan')} → {q['ke']}, "
                     f"{rules.when_label(when, now)}")
        rows.append([(f"📤 Kirim #{q['id']} sekarang", f"q:now:{q['id']}"), (f"❌ Batalkan #{q['id']}", f"q:del:{q['id']}")])
    return f"{note}🕗 Antrean kirim\n\n" + "\n".join(lines), kb(*rows, [("⬅️ Daftar lamaran", "lamlist")])


async def btn_queue(query, context, uid, data, arg):
    """q:list, q:now:<id>, q:del:<id>"""
    parts = arg.split(":")
    if parts[0] == "list":
        await safe_edit(query.message, *queue_view(data))
        return
    qid = int(parts[1])
    await query.edit_message_reply_markup(None)
    if not any(q["id"] == qid for q in data.get("antrian") or []):
        await query.message.reply_text("Lamaran ini sudah tidak ada di antrean (mungkin sudah terkirim atau dibatalkan).")
        return
    unschedule_queued(context.job_queue, uid, qid)
    if parts[0] == "del":
        update_user(uid, lambda d: d.__setitem__("antrian", [q for q in d.get("antrian") or [] if q["id"] != qid]))
        await query.message.reply_text("Dibatalkan. Lamaran ini tidak akan dikirim.")
        return
    await query.message.reply_text("⏳ Mengirim sekarang...")
    await deliver_queued(context.bot, uid, qid)


async def btn_edit_recipient(query, context, uid, data, arg):
    context.user_data["awaiting_email"] = True
    await query.edit_message_reply_markup(None)
    await query.message.reply_text("Ketik alamat email tujuan yang benar:")


async def btn_cancel_send(query, context, uid, data, arg):
    context.user_data["recipient"] = None
    await query.edit_message_reply_markup(None)
    await query.message.reply_text("Dibatalkan. Tidak ada email yang dikirim.")


BUTTONS = {
    "skip": btn_skip, "gaya": btn_gaya, "set": btn_settings, "acc": btn_accounts, "ai": btn_ai,
    "font": btn_font, "par": btn_par, "cv": btn_cv, "tl": btn_tailor_result, "gap": btn_gap,
    "pref": btn_pref, "iv": btn_iv, "q": btn_queue, "bk": btn_backup,
    "lamlist": btn_lamaran_list,
    "app": btn_app_detail, "st": btn_status,
    "str": lambda *a: btn_status(*a, new_message=True),
    "fu": btn_followup_draft, "fusend": btn_followup_send, "fucancel": btn_followup_cancel,
    "rec": btn_recommendation, "retryjob": btn_retry_job,
    "tailor": btn_tailor, "orig": btn_original_cv, "cl": btn_cover_letter, "send": btn_send,
    "confirm": btn_confirm_send, "editto": btn_edit_recipient, "cancel": btn_cancel_send,
}
NEEDS_JOB = {"tailor", "orig", "cl", "send", "confirm", "editto", "cancel", "tl"}


@only_allowed
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name, _, arg = query.data.partition(":")
    handler = BUTTONS.get(name)
    if handler is None:
        log.warning("tombol tidak dikenal: %s", query.data)
        return
    if name in NEEDS_JOB and not context.user_data.get("job"):
        await query.message.reply_text("Sesi lowongan sudah habis. Kirim ulang lowongannya ya.")
        return
    uid = update.effective_user.id
    await handler(query, context, uid, load_user(uid), arg)


# ================================================================ main
async def post_init(app: Application):
    await app.bot.set_my_commands([BotCommand(name, desc) for name, desc in COMMANDS])


def main():
    if not ALLOWED:
        log.warning("ALLOWED_USER_IDS kosong: SIAPA PUN bisa memakai bot ini, dan cek harian tidak aktif!")

    persistence = PicklePersistence(filepath=str(DATA_DIR / "bot_state.pkl"))
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).persistence(persistence) \
        .post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["cv", "profil"], profil))
    app.add_handler(CommandHandler("revisi", revisi))
    app.add_handler(CommandHandler(["lamaran", "riwayat"], lamaran))
    app.add_handler(CommandHandler("statistik", statistik))
    app.add_handler(CommandHandler("gaya", gaya))
    app.add_handler(CommandHandler(["pengaturan", "config"], pengaturan))
    app.add_handler(CommandHandler("cekbalasan", cekbalasan))
    app.add_handler(CommandHandler("rekomendasi", rekomendasi))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_button))

    if app.job_queue and ALLOWED:
        hour, minute = (int(x) for x in DAILY_CHECK_TIME.split(":"))
        when = dt.time(hour, minute, tzinfo=ZoneInfo(TIMEZONE))
        for uid in ALLOWED:
            app.job_queue.run_daily(daily_check, when, chat_id=uid, user_id=uid, name=f"daily_{uid}")
            app.job_queue.run_repeating(reply_check_job, interval=REPLY_CHECK_MINUTES * 60, first=60,
                                        chat_id=uid, user_id=uid, name=f"replies_{uid}")
        log.info("Rekomendasi & pengingat harian jam %s (%s), cek balasan HR tiap %s menit",
                 DAILY_CHECK_TIME, TIMEZONE, REPLY_CHECK_MINUTES)
        for uid in ALLOWED:  # lamaran terjadwal yang belum terkirim (misalnya setelah bot restart)
            for item in load_user(uid).get("antrian") or []:
                schedule_queued(app.job_queue, uid, item)

    log.info("Bot berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
