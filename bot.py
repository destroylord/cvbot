"""Bot Telegram: scan CV, rekomendasi lowongan dari Gmail job alert, CV tailoring,
cover letter, dan kirim lamaran via Gmail (semi-otomatis: selalu minta konfirmasi)."""
import asyncio
import datetime as dt
import hashlib
import logging
import os
import re
import sys
import time
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
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
import cv_parser  # noqa: E402
import gmail_accounts  # noqa: E402
import gmail_replies  # noqa: E402
import job_alerts  # noqa: E402
import job_fetcher  # noqa: E402
import mailer  # noqa: E402
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
    ("profil", "Lihat hasil scan CV"),
    ("pengaturan", "Gaya cover letter, akun Gmail, notifikasi"),
    ("revisi", "Revisi cover letter / follow-up terakhir"),
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
        [["🎯 Rekomendasi", "📂 Lamaran"], ["📬 Cek Balasan", "📊 Statistik"], ["👤 Profil", "⚙️ Pengaturan"]],
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
            f"Email HR: {job.get('email_hr') or 'tidak ditemukan'}")


# ================================================================ commands
@only_allowed
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Cara pakainya:\n\n"
        "1. Kirim CV kamu (PDF/DOCX)\n"
        "2. Kirim link / teks / foto poster lowongan (bisa di-forward dari grup lain), "
        "atau tekan 🎯 Rekomendasi untuk cari dari email job alert\n"
        f"3. Kalau skornya di bawah {TAILOR_THRESHOLD}, CV otomatis disesuaikan (tetap jujur)\n"
        "4. Buat cover letter, review, lalu kirim via Gmail dengan satu klik\n\n"
        "Pakai tombol menu di bawah, atau tombol Menu di samping kolom chat untuk daftar perintah. "
        "Revisi cover letter: /revisi <instruksi>\n\n"
        f"Otomatis: rekomendasi & pengingat follow-up tiap hari jam {DAILY_CHECK_TIME}, "
        f"cek balasan HR tiap {REPLY_CHECK_MINUTES} menit (bisa dimatikan di ⚙️ Pengaturan).",
        reply_markup=main_menu())


@only_allowed
async def profil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = load_user(update.effective_user.id).get("profile")
    if not profile:
        await update.message.reply_text("Belum ada CV. Kirim dulu file PDF/DOCX kamu.")
        return
    skills = ", ".join(profile.get("skill") or []) or "-"
    exp = "\n".join(f"- {e.get('posisi')} @ {e.get('perusahaan')} ({e.get('periode')})"
                    for e in profile.get("pengalaman") or []) or "-"
    await update.message.reply_text(
        f"Nama: {profile.get('nama')}\nEmail: {profile.get('email')}\n\n"
        f"Ringkasan: {profile.get('ringkasan')}\n\nSkill: {skills}\n\nPengalaman:\n{exp}")


# ================================================================ tracker lamaran
def lamaran_list_view(data: dict):
    apps = data.get("lamaran") or []
    if not apps:
        return "Belum ada lamaran yang terkirim lewat bot.", None
    recent = apps[-10:][::-1]
    lines = [f"#{a['id']} {tracker.STATUS.get(a['status'], a['status'])} · {a.get('posisi')} - "
             f"{a.get('perusahaan')} ({tracker.days_since(a, TIMEZONE)} hari)" for a in recent]
    buttons = [(f"#{a['id']} {(a.get('perusahaan') or '-')[:18]}", f"app:{a['id']}") for a in recent]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    text = (f"📂 Lamaran kamu ({len(apps)} total, 10 terbaru):\n\n" + "\n".join(lines) +
            "\n\nPilih lamaran untuk lihat detail, ubah status, atau buat follow-up.")
    return text, kb(*rows)


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
        [("📨 Draft Follow-up", f"fu:{i}")],
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
    return (f"Gaya cover letter sekarang: {GAYA_LABEL.get(current)}\n\n"
            "🎩 Formal: sopan dan profesional, 250-300 kata\n"
            "😊 Santai: profesional tapi hangat, 200-250 kata\n"
            "⚡ Singkat: langsung ke poin, maksimal 150 kata",
            kb([(label, f"gaya:{key}") for key, label in GAYA_LABEL.items()]))


@only_allowed
async def gaya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = gaya_view(load_user(update.effective_user.id))
    await update.message.reply_text(text, reply_markup=markup)


TOGGLES = {"auto_rekomendasi": "🎯 Rekomendasi harian", "auto_balasan": "📬 Cek balasan otomatis"}


def settings_view(data: dict):
    accounts = gmail_accounts.load()
    default = default_account(data)
    onoff = {key: "Aktif" if data.get(key, True) else "Mati" for key in TOGGLES}
    ai = ai_helper.PROVIDERS.get(ai_helper.provider(), {})
    text = (f"⚙️ Pengaturan\n\n"
            f"AI: {ai.get('label', ai_helper.provider()).split(' (')[0]} · {ai_helper.current_model() or '-'}\n"
            f"Gaya cover letter: {GAYA_LABEL.get(data.get('gaya', 'formal'))}\n"
            f"Akun Gmail default: {default.address if default else 'belum ada'}"
            f"{f' ({len(accounts)} akun terhubung)' if len(accounts) > 1 else ''}\n"
            f"Rekomendasi harian jam {DAILY_CHECK_TIME}: {onoff['auto_rekomendasi']}\n"
            f"Cek balasan HR tiap {REPLY_CHECK_MINUTES} menit: {onoff['auto_balasan']}\n\n"
            f"Jam dan ambang skor diatur di file .env.")
    rows = [[("🤖 Model AI", "ai")], [("✍️ Gaya cover letter", "set:gaya")], [("📧 Akun Gmail", "acc")]]
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
        kind, current, label = "cl", context.user_data["cover_letter"], "cover letter"
    else:
        await update.message.reply_text("Belum ada cover letter atau follow-up untuk direvisi.")
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
    top = top_jobs(unique)
    if not top:
        await finish_progress(progress, f"Tidak ada rekomendasi baru dengan skor ≥ {MIN_REC_SCORE} "
                                        f"({len(emails)} email baru dicek, {len(unique)} lowongan ditemukan).")
        return
    await finish_progress(progress)

    recs = context.user_data.setdefault("recs", {})
    recs.update({rec_id(job): job for job in top})
    while len(recs) > 50:
        recs.pop(next(iter(recs)))
    text, rows = recommendations_message(top)
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
        await bot.send_message(chat_id, reply_message(a, reply, info, changed),
                               reply_markup=kb([("📋 Lihat detail lamaran", f"app:{a['id']}")]),
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
async def process_job(message, context, uid: int, job_text: str):
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
        await msg.edit_text(f"Gagal menganalisis lowongan: {e}", reply_markup=kb([("🔄 Coba lagi", "retryjob")]))
        return
    context.user_data["retry_job_text"] = None
    if job.get("bukan_lowongan"):
        await msg.edit_text("Sepertinya itu bukan halaman/teks lowongan kerja. "
                            "Coba copy-paste deskripsi lowongannya langsung ke sini.")
        return

    context.user_data.update({
        "job": job, "job_text": job_text, "active_profile": profile, "tailored_cv": None,
        "cover_letter": None, "recipient": None, "awaiting_email": False,
    })

    if int(job.get("skor") or 0) < TAILOR_THRESHOLD:
        await msg.edit_text(job_summary(job) + f"\n\n🛠️ Skor di bawah {TAILOR_THRESHOLD}, aku sesuaikan CV kamu dulu...")
        await tailor_flow(message, context, uid)
    else:
        await msg.edit_text(job_summary(job), reply_markup=kb(
            [("✍️ Buat Cover Letter", "cl"), ("🛠️ Sesuaikan CV", "tailor")],
            [("❌ Lewati", "skip")]))


async def tailor_flow(message, context, uid: int):
    original = load_user(uid)["profile"]
    job = context.user_data["job"]
    job_text = context.user_data["job_text"]
    lang = job.get("bahasa_lowongan") or "id"
    old_score = int(job.get("skor") or 0)

    msg = await message.reply_text("⏳ Menyesuaikan CV dengan lowongan...")
    try:
        result = await asyncio.to_thread(ai_helper.tailor_cv, original, job_text, lang)
        tailored = ai_helper.guard_profile(original, result.get("profile") or {})
        await msg.edit_text("⏳ Menilai ulang CV yang sudah disesuaikan...")
        new_job = await asyncio.to_thread(ai_helper.analyze_job, tailored, job_text)
        filename = f"CV_{safe_name(original.get('nama') or 'Kandidat')}_{safe_name(job.get('posisi') or 'Lamaran')}.pdf"
        path = user_dir(uid) / filename
        await asyncio.to_thread(cv_builder.build_pdf, tailored, str(path), lang)
    except Exception as e:
        log.exception("tailoring gagal")
        await msg.edit_text(f"Gagal menyesuaikan CV: {e}", reply_markup=kb(
            [("✍️ Pakai CV Asli & Buat Cover Letter", "cl")]))
        return

    new_score = int(new_job.get("skor") or old_score)
    job["skor"] = new_score
    job["skill_kurang"] = new_job.get("skill_kurang") or job.get("skill_kurang")
    context.user_data.update({"active_profile": tailored, "tailored_cv": str(path)})

    changes = "\n".join(f"• {c}" for c in result.get("perubahan") or []) or "-"
    learn = ", ".join(result.get("saran_belajar") or []) or "-"
    note = ""
    if new_score < 50:
        note = ("\n\n⚠️ Skornya masih rendah walaupun CV sudah disesuaikan. "
                "Lowongan ini mungkin memang kurang cocok, pertimbangkan untuk dilewati.")
    await msg.edit_text(
        f"🛠️ CV sudah disesuaikan\nSkor: {old_score} → {new_score}\n\n"
        f"Yang diubah:\n{changes}\n\n"
        f"📚 Belum kamu miliki (TIDAK ditambahkan ke CV, bisa dipelajari):\n{learn}{note}")
    with open(path, "rb") as f:
        await message.reply_document(
            f, filename=path.name,
            caption="Cek dulu CV ini ya. Isinya hanya diambil dari CV asli kamu.",
            reply_markup=kb([("✍️ Buat Cover Letter", "cl")],
                            [("↩️ Pakai CV Asli", "orig"), ("❌ Lewati", "skip")]))


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
    if ext not in (".pdf", ".docx"):
        await update.message.reply_text("Kirim CV dalam format PDF/DOCX, atau gambar lowongan (JPG/PNG).")
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
    except Exception as e:
        log.exception("gagal proses CV")
        await msg.edit_text(f"Gagal memproses CV: {e}")
        return

    data = load_user(uid)
    data.update({"cv_path": str(path), "profile": profile})
    save_user(uid, data)
    context.user_data.update({"job": None, "active_profile": profile, "tailored_cv": None})
    await msg.edit_text(
        f"✅ CV tersimpan, {profile.get('nama') or 'kamu'}!\n"
        f"Skill terdeteksi: {', '.join((profile.get('skill') or [])[:10]) or '-'}\n\n"
        "Sekarang kirim link / teks lowongan, atau ketik /rekomendasi.")


# ================================================================ teks masuk
MENU_ACTIONS = {
    "🎯 Rekomendasi": rekomendasi, "📂 Lamaran": lamaran, "📬 Cek Balasan": cekbalasan,
    "📊 Statistik": statistik, "👤 Profil": profil, "⚙️ Pengaturan": pengaturan,
}


@only_allowed
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text in MENU_ACTIONS:
        context.user_data.update({"awaiting_email": False, "gmail_form": None, "ai_form": None})
        await MENU_ACTIONS[text](update, context)
        return

    if context.user_data.get("gmail_form"):
        await handle_gmail_form(update.message, context, uid, text)
        return

    if context.user_data.get("ai_form"):
        await handle_ai_form(update.message, context, uid, text)
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

    await process_job(update.message, context, uid, job_text)


# ================================================================ kirim email
def cover_letter_buttons():
    return kb([("📧 Kirim via Gmail", "send"), ("🔄 Buat Ulang", "cl")])


def email_subject(job: dict, name: str) -> str:
    subject = f"Lamaran {job.get('posisi') or 'Pekerjaan'}"
    return f"{subject} - {name}" if name else subject


def attachment_path(context, uid: int) -> str | None:
    return context.user_data.get("tailored_cv") or load_user(uid).get("cv_path")


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
    await message.reply_text(
        f"Konfirmasi pengiriman:\n\n"
        f"Ke: {context.user_data['recipient']}\n"
        f"Subjek: {email_subject(job, name)}\n"
        f"Lampiran: {Path(attach).name if attach else '-'} ({cv_type})\n"
        f"Isi: cover letter di atas\n\n"
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
    markup = kb([("🔄 Buat ulang cover letter", "cl")]) if context.user_data.get("job") else None
    await safe_edit(query.message, f"✅ Gaya cover letter diubah ke {GAYA_LABEL.get(style)}.", markup)


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
    await process_job(query.message, context, uid, job_text)


async def btn_retry_job(query, context, uid, data, arg):
    job_text = context.user_data.get("retry_job_text")
    await query.edit_message_reply_markup(None)
    if not job_text:
        await query.message.reply_text("Lowongan ini sudah tidak tersimpan. Kirim ulang lowongannya ya.")
        return
    await process_job(query.message, context, uid, job_text)


# ---- tombol di bawah ini butuh lowongan yang sedang diproses (context.user_data["job"])
async def btn_tailor(query, context, uid, data, arg):
    await query.edit_message_reply_markup(None)
    await tailor_flow(query.message, context, uid)


async def btn_original_cv(query, context, uid, data, arg):
    context.user_data.update({"active_profile": data.get("profile"), "tailored_cv": None})
    await query.edit_message_reply_markup(None)
    await query.message.reply_text("Oke, pakai CV asli.", reply_markup=kb([("✍️ Buat Cover Letter", "cl")]))


async def btn_cover_letter(query, context, uid, data, arg):
    profile = context.user_data.get("active_profile") or data.get("profile")
    style = data.get("gaya", "formal")
    msg = await query.message.reply_text(f"⏳ Menulis cover letter ({GAYA_LABEL.get(style)})...")
    try:
        cl = await asyncio.to_thread(
            ai_helper.write_cover_letter, profile, context.user_data["job"], context.user_data["job_text"], style)
    except Exception as e:
        log.exception("cover letter gagal")
        await msg.edit_text(f"Gagal membuat cover letter: {e}")
        return
    context.user_data["cover_letter"] = cl
    context.user_data["draft_kind"] = "cl"
    await msg.edit_text(cl, reply_markup=cover_letter_buttons())
    await query.message.reply_text("Mau diubah? Pakai /revisi <instruksi>, atau /gaya untuk ganti gaya.")


async def btn_send(query, context, uid, data, arg):
    if not context.user_data.get("cover_letter"):
        await query.message.reply_text("Buat cover letter dulu.")
        return
    if not gmail_configured():
        await query.message.reply_text(GMAIL_MISSING)
        return
    job = context.user_data["job"]
    if job.get("email_hr"):
        context.user_data["recipient"] = job["email_hr"]
        await show_send_confirmation(query.message, context, uid)
    else:
        context.user_data["awaiting_email"] = True
        await query.message.reply_text("Email HR tidak ditemukan di lowongan. Ketik alamat email tujuannya:")


async def btn_confirm_send(query, context, uid, data, arg):
    """confirm:<indeks akun Gmail>"""
    recipient = context.user_data.get("recipient")
    if not recipient:
        await query.message.reply_text("Alamat tujuan belum ada.")
        return
    accounts = gmail_accounts.load()
    idx = int(arg or 0)
    if idx >= len(accounts):
        await query.message.reply_text("Daftar akun Gmail sudah berubah. Tekan 📧 Kirim via Gmail lagi.")
        return
    account = accounts[idx]
    job = context.user_data["job"]
    await query.edit_message_reply_markup(None)
    msg = await query.message.reply_text(f"⏳ Mengirim email dari {account.address}...")
    subject = email_subject(job, (data.get("profile") or {}).get("nama") or "")
    try:
        message_id = await asyncio.to_thread(
            mailer.send_application, account, recipient, subject,
            context.user_data["cover_letter"], attachment_path(context, uid))
    except Exception as e:
        log.exception("kirim email gagal")
        where = "file .env" if account.from_env else "⚙️ Pengaturan > 📧 Akun Gmail"
        await msg.edit_text(f"Gagal mengirim: {e}\nCek app password {account.address} di {where}.")
        return
    info = {
        "posisi": job.get("posisi"), "perusahaan": job.get("perusahaan"), "ke": recipient,
        "dari": account.address,
        "skor": job.get("skor"), "bahasa": job.get("bahasa_lowongan"),
        "cv_disesuaikan": bool(context.user_data.get("tailored_cv")), "subjek": subject,
        "message_id": message_id, "gaya": data.get("gaya", "formal"),
        "job_text": (context.user_data.get("job_text") or "")[:4000],
    }
    app = update_user(uid, lambda d: tracker.add_application(d, info, TIMEZONE))
    context.user_data["cover_letter"] = None
    await msg.edit_text(
        f"✅ Lamaran #{app['id']} terkirim ke {recipient} dari {account.address}. Semoga lolos! 🍀\n\n"
        f"Balasan HR akan dicek otomatis, dan kalau {FOLLOWUP_DAYS} hari belum ada kabar "
        f"aku ingatkan untuk follow-up. Lihat semua lamaran di /lamaran.")


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
    "lamlist": btn_lamaran_list,
    "app": btn_app_detail, "st": btn_status,
    "str": lambda *a: btn_status(*a, new_message=True),
    "fu": btn_followup_draft, "fusend": btn_followup_send, "fucancel": btn_followup_cancel,
    "rec": btn_recommendation, "retryjob": btn_retry_job,
    "tailor": btn_tailor, "orig": btn_original_cv, "cl": btn_cover_letter, "send": btn_send,
    "confirm": btn_confirm_send, "editto": btn_edit_recipient, "cancel": btn_cancel_send,
}
NEEDS_JOB = {"tailor", "orig", "cl", "send", "confirm", "editto", "cancel"}


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
    app.add_handler(CommandHandler("profil", profil))
    app.add_handler(CommandHandler("revisi", revisi))
    app.add_handler(CommandHandler(["lamaran", "riwayat"], lamaran))
    app.add_handler(CommandHandler("statistik", statistik))
    app.add_handler(CommandHandler("gaya", gaya))
    app.add_handler(CommandHandler(["pengaturan", "config"], pengaturan))
    app.add_handler(CommandHandler("cekbalasan", cekbalasan))
    app.add_handler(CommandHandler("rekomendasi", rekomendasi))
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

    log.info("Bot berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
