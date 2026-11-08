"""Bot Telegram: scan CV, rekomendasi lowongan dari Gmail job alert, CV tailoring,
cover letter, dan kirim lamaran via Gmail (semi-otomatis: selalu minta konfirmasi)."""
import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update  # noqa: E402
from telegram.error import BadRequest  # noqa: E402
from telegram.ext import (  # noqa: E402
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, PicklePersistence, filters,
)

import ai_helper  # noqa: E402
import cv_builder  # noqa: E402
import cv_parser  # noqa: E402
import gmail_replies  # noqa: E402
import job_alerts  # noqa: E402
import job_fetcher  # noqa: E402
import mailer  # noqa: E402
import tracker  # noqa: E402

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("cvbot")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
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

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
URL_RE = re.compile(r"https?://\S+")


# ================================================================ util
def load_user(uid: int) -> dict:
    p = DATA_DIR / f"{uid}.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    tracker.migrate(data, TIMEZONE)
    return data


def save_user(uid: int, data: dict) -> None:
    (DATA_DIR / f"{uid}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def gmail_configured() -> bool:
    return bool(os.getenv("GMAIL_ADDRESS") and os.getenv("GMAIL_APP_PASSWORD"))


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
        "2. Kirim link / teks lowongan, atau ketik /rekomendasi untuk cari dari email job alert\n"
        f"3. Kalau skornya di bawah {TAILOR_THRESHOLD}, CV otomatis disesuaikan (tetap jujur)\n"
        "4. Buat cover letter, review, lalu kirim via Gmail dengan satu klik\n\n"
        "Perintah:\n"
        "/rekomendasi - cek lowongan dari email job alert\n"
        "/lamaran - daftar lamaran, ubah status, follow-up\n"
        "/statistik - statistik lamaran kamu\n"
        "/cekbalasan - cek balasan HR di Gmail sekarang\n"
        "/gaya - atur gaya cover letter\n"
        "/profil - lihat hasil scan CV\n"
        "/revisi <instruksi> - revisi cover letter / follow-up terakhir\n\n"
        f"Otomatis: rekomendasi & pengingat follow-up tiap hari jam {DAILY_CHECK_TIME}, "
        f"cek balasan HR tiap {REPLY_CHECK_MINUTES} menit."
    )


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


@only_allowed
async def gaya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = load_user(update.effective_user.id).get("gaya", "formal")
    await update.message.reply_text(
        f"Gaya cover letter sekarang: {GAYA_LABEL.get(current)}\n\n"
        "🎩 Formal: sopan dan profesional, 250-300 kata\n"
        "😊 Santai: profesional tapi hangat, 200-250 kata\n"
        "⚡ Singkat: langsung ke poin, maksimal 150 kata",
        reply_markup=kb([(label, f"gaya:{key}") for key, label in GAYA_LABEL.items()]))


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
        await say("GMAIL_ADDRESS / GMAIL_APP_PASSWORD belum diisi di .env.")
        return

    await say("⏳ Mengecek email job alert...")
    seen = list(data.get("gmail_seen") or [])
    try:
        emails = await asyncio.to_thread(job_alerts.fetch_alert_emails, set(seen), LOOKBACK_DAYS)
    except Exception as e:
        log.exception("gagal membaca Gmail")
        await say(f"Gagal membaca Gmail: {e}")
        return

    jobs = []
    for em in emails:
        try:
            found = await asyncio.to_thread(ai_helper.extract_and_rank, data["profile"], em["text"], em["from"])
        except Exception:
            log.exception("gagal memproses email %s", em["subject"])
            continue  # tidak ditandai 'seen' supaya dicoba lagi nanti
        for job in found:
            ref = job.get("link_ref")
            job["link"] = em["links"].get(ref) if ref else None
            job["sumber"] = em["from"]
            jobs.append(job)
        seen.append(em["id"])

    data = load_user(uid)
    data["gmail_seen"] = seen[-500:]
    save_user(uid, data)

    unique = {}
    for job in jobs:
        key = (str(job.get("posisi")).lower(), str(job.get("perusahaan")).lower())
        if key not in unique or (job.get("skor") or 0) > (unique[key].get("skor") or 0):
            unique[key] = job
    top = sorted((j for j in unique.values() if (j.get("skor") or 0) >= MIN_REC_SCORE),
                 key=lambda j: j.get("skor") or 0, reverse=True)[:MAX_RECS]

    if not top:
        await say(f"Tidak ada rekomendasi baru dengan skor ≥ {MIN_REC_SCORE} "
                  f"({len(emails)} email baru dicek, {len(unique)} lowongan ditemukan).")
        return

    recs = context.user_data.setdefault("recs", {})
    await bot.send_message(chat_id, f"🎯 {len(top)} lowongan yang cocok dengan CV kamu:")
    for job in top:
        rid = hashlib.md5(f"{job.get('posisi')}|{job.get('perusahaan')}|{job.get('link')}".encode()).hexdigest()[:10]
        recs[rid] = job
        text = (f"{job.get('skor')}/100 · {job.get('posisi')}\n"
                f"🏢 {job.get('perusahaan')}" + (f" · {job.get('lokasi')}" if job.get("lokasi") else "") +
                f"\n💬 {job.get('alasan') or ''}")
        buttons = [InlineKeyboardButton("🔍 Proses", callback_data=f"rec:{rid}")]
        link = job.get("link") or ""
        if link.startswith("http"):
            buttons.append(InlineKeyboardButton("🌐 Buka", url=link))
        try:
            await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup([buttons]))
        except BadRequest:
            await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup([buttons[:1]]))
    while len(recs) > 50:
        recs.pop(next(iter(recs)))


async def daily_check(context: ContextTypes.DEFAULT_TYPE):
    uid, chat_id = context.job.user_id, context.job.chat_id
    try:
        await send_recommendations(context, uid, chat_id, silent_if_empty=True)
    except Exception:
        log.exception("cek rekomendasi harian gagal")
    try:
        await send_followup_reminders(context, uid, chat_id)
    except Exception:
        log.exception("pengingat follow-up gagal")


async def reply_check_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await check_replies(context, context.job.user_id, context.job.chat_id)
    except Exception:
        log.exception("cek balasan HR gagal")


# ================================================================ follow-up & balasan HR
async def send_followup_reminders(context, uid: int, chat_id: int):
    due = update_user(uid, lambda d: tracker.pop_due_reminders(d, FOLLOWUP_DAYS, TIMEZONE))
    for app in due:
        await context.bot.send_message(
            chat_id,
            f"⏰ Sudah {tracker.days_since(app, TIMEZONE)} hari belum ada kabar dari lamaran #{app['id']}\n"
            f"{app.get('posisi')} - {app.get('perusahaan')}\n\nMau kirim email follow-up?",
            reply_markup=kb([("📨 Buat Draft Follow-up", f"fu:{app['id']}")],
                            [("👻 Tandai tidak ada kabar", f"st:{app['id']}:tidak_ada_kabar")]))


async def check_replies(context, uid: int, chat_id: int, manual: bool = False):
    bot = context.bot

    async def say(text):
        if manual:
            await bot.send_message(chat_id, text)

    if not gmail_configured():
        await say("GMAIL_ADDRESS / GMAIL_APP_PASSWORD belum diisi di .env.")
        return
    data = load_user(uid)
    apps = [a for a in data.get("lamaran") or []
            if a.get("status") in tracker.CHECK_REPLIES and a.get("ke")
            and tracker.days_since(a, TIMEZONE) <= 60]
    if not apps:
        await say("Tidak ada lamaran aktif yang perlu dicek balasannya.")
        return

    await say("⏳ Mengecek balasan HR di Gmail...")
    sent_dates = {a["id"]: tracker.sent_at(a, TIMEZONE) for a in apps}
    try:
        replies = await asyncio.to_thread(
            gmail_replies.find_replies, apps, set(data.get("reply_seen") or []), sent_dates)
    except Exception as e:
        log.exception("gagal membaca balasan Gmail")
        await say(f"Gagal membaca Gmail: {e}")
        return

    found = 0
    for reply in replies:
        app = next(a for a in apps if a["id"] == reply["app_id"])
        try:
            info = await asyncio.to_thread(
                ai_helper.classify_reply, app, reply["from"], reply["subject"], reply["text"])
        except Exception:
            log.exception("gagal mengklasifikasi balasan")
            continue  # tidak ditandai 'seen' supaya dicoba lagi

        def apply(d, reply=reply, info=info, app_id=app["id"]):
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

        result = update_user(uid, apply)
        if not result:
            continue
        found += 1
        a, changed = result
        text = (f"📬 Ada balasan untuk lamaran #{a['id']}\n{a.get('posisi')} - {a.get('perusahaan')}\n\n"
                f"Dari: {reply['from']}\nSubjek: {reply['subject']}\n"
                f"Jenis: {JENIS_LABEL.get(info.get('jenis'), info.get('jenis'))}\n\n{info.get('ringkasan') or ''}")
        if info.get("jadwal"):
            text += f"\n\n📅 Jadwal/tenggat: {info['jadwal']}"
        if info.get("perlu_tindakan"):
            text += f"\n👉 Perlu dilakukan: {info['perlu_tindakan']}"
        if changed:
            text += f"\n\nStatus otomatis diubah ke {tracker.STATUS[a['status']]} (bisa diganti lewat /lamaran)."
        await bot.send_message(chat_id, text, reply_markup=kb([("📋 Lihat detail lamaran", f"app:{a['id']}")]))

    if manual and not found:
        await bot.send_message(chat_id, "Belum ada balasan baru dari HR.")


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
        await msg.edit_text(f"Gagal menganalisis lowongan: {e}")
        return
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


# ================================================================ upload CV
@only_allowed
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    ext = Path(doc.file_name or "").suffix.lower()
    if ext not in (".pdf", ".docx"):
        await update.message.reply_text("Kirim CV dalam format PDF atau DOCX ya.")
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
@only_allowed
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

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
    await message.reply_text(
        f"Konfirmasi pengiriman:\n\n"
        f"Ke: {context.user_data['recipient']}\n"
        f"Subjek: {email_subject(job, name)}\n"
        f"Lampiran: {Path(attach).name if attach else '-'} ({cv_type})\n"
        f"Isi: cover letter di atas\n\n"
        f"Posisi: {job.get('posisi')} - {job.get('perusahaan')}",
        reply_markup=kb([("✅ Ya, kirim", "confirm"), ("❌ Batal", "cancel")]))


# ================================================================ tombol
@only_allowed
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    uid = update.effective_user.id
    data = load_user(uid)

    if action == "skip":
        await query.edit_message_reply_markup(None)
        await query.message.reply_text("Oke, dilewati. Kirim lowongan berikutnya kapan saja.")
        return

    if action.startswith("gaya:"):
        style = action.split(":", 1)[1]
        update_user(uid, lambda d: d.__setitem__("gaya", style))
        markup = kb([("🔄 Buat ulang cover letter", "cl")]) if context.user_data.get("job") else None
        await safe_edit(query.message, f"✅ Gaya cover letter diubah ke {GAYA_LABEL.get(style)}.", markup)
        return

    if action == "lamlist":
        text, markup = lamaran_list_view(data)
        await safe_edit(query.message, text, markup)
        return

    if action.startswith("app:"):
        app = tracker.find(data, int(action.split(":")[1]))
        if not app:
            await query.message.reply_text("Lamaran tidak ditemukan.")
            return
        text, markup = app_detail_view(app)
        await query.message.reply_text(text, reply_markup=markup)
        return

    if action.startswith("st:"):
        _, app_id, status = action.split(":", 2)

        def change(d):
            a = tracker.find(d, int(app_id))
            if a:
                tracker.set_status(a, status, TIMEZONE)
            return dict(a) if a else None

        app = update_user(uid, change)
        if app:
            text, markup = app_detail_view(app)
            await safe_edit(query.message, text, markup)
        return

    if action.startswith("fu:"):
        app = tracker.find(data, int(action.split(":")[1]))
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
        return

    if action == "fusend":
        followup = context.user_data.get("followup")
        app = tracker.find(data, followup["app_id"]) if followup else None
        if not app:
            await query.message.reply_text("Draft follow-up sudah tidak ada. Buat ulang lewat /lamaran.")
            return
        if not gmail_configured():
            await query.message.reply_text("GMAIL_ADDRESS / GMAIL_APP_PASSWORD belum diisi di .env.")
            return
        await query.edit_message_reply_markup(None)
        msg = await query.message.reply_text("⏳ Mengirim follow-up...")
        subject = app.get("subjek") or email_subject(app, (data.get("profile") or {}).get("nama") or "")
        try:
            await asyncio.to_thread(mailer.send_application, app["ke"], f"Re: {subject}",
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
        return

    if action == "fucancel":
        context.user_data.update({"followup": None, "draft_kind": None})
        await query.edit_message_reply_markup(None)
        await query.message.reply_text("Follow-up dibatalkan.")
        return

    if action.startswith("rec:"):
        rec = (context.user_data.get("recs") or {}).get(action[4:])
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
        return

    job = context.user_data.get("job")
    if not job:
        await query.message.reply_text("Sesi lowongan sudah habis. Kirim ulang lowongannya ya.")
        return

    if action == "tailor":
        await query.edit_message_reply_markup(None)
        await tailor_flow(query.message, context, uid)

    elif action == "orig":
        context.user_data.update({"active_profile": data.get("profile"), "tailored_cv": None})
        await query.edit_message_reply_markup(None)
        await query.message.reply_text("Oke, pakai CV asli.", reply_markup=kb([("✍️ Buat Cover Letter", "cl")]))

    elif action == "cl":
        profile = context.user_data.get("active_profile") or data.get("profile")
        style = data.get("gaya", "formal")
        msg = await query.message.reply_text(f"⏳ Menulis cover letter ({GAYA_LABEL.get(style)})...")
        try:
            cl = await asyncio.to_thread(
                ai_helper.write_cover_letter, profile, job, context.user_data["job_text"], style)
        except Exception as e:
            log.exception("cover letter gagal")
            await msg.edit_text(f"Gagal membuat cover letter: {e}")
            return
        context.user_data["cover_letter"] = cl
        context.user_data["draft_kind"] = "cl"
        await msg.edit_text(cl, reply_markup=cover_letter_buttons())
        await query.message.reply_text("Mau diubah? Pakai /revisi <instruksi>, atau /gaya untuk ganti gaya.")

    elif action == "send":
        if not context.user_data.get("cover_letter"):
            await query.message.reply_text("Buat cover letter dulu.")
            return
        if not gmail_configured():
            await query.message.reply_text("GMAIL_ADDRESS / GMAIL_APP_PASSWORD belum diisi di .env.")
            return
        if job.get("email_hr"):
            context.user_data["recipient"] = job["email_hr"]
            await show_send_confirmation(query.message, context, uid)
        else:
            context.user_data["awaiting_email"] = True
            await query.message.reply_text("Email HR tidak ditemukan di lowongan. Ketik alamat email tujuannya:")

    elif action == "confirm":
        recipient = context.user_data.get("recipient")
        if not recipient:
            await query.message.reply_text("Alamat tujuan belum ada.")
            return
        await query.edit_message_reply_markup(None)
        msg = await query.message.reply_text("⏳ Mengirim email...")
        name = (data.get("profile") or {}).get("nama") or ""
        subject = email_subject(job, name)
        try:
            message_id = await asyncio.to_thread(
                mailer.send_application, recipient, subject,
                context.user_data["cover_letter"], attachment_path(context, uid))
        except Exception as e:
            log.exception("kirim email gagal")
            await msg.edit_text(f"Gagal mengirim: {e}\nCek GMAIL_ADDRESS dan GMAIL_APP_PASSWORD di .env")
            return
        info = {
            "posisi": job.get("posisi"), "perusahaan": job.get("perusahaan"), "ke": recipient,
            "skor": job.get("skor"), "bahasa": job.get("bahasa_lowongan"),
            "cv_disesuaikan": bool(context.user_data.get("tailored_cv")), "subjek": subject,
            "message_id": message_id, "gaya": data.get("gaya", "formal"),
            "job_text": (context.user_data.get("job_text") or "")[:4000],
        }
        app = update_user(uid, lambda d: tracker.add_application(d, info, TIMEZONE))
        context.user_data["cover_letter"] = None
        await msg.edit_text(
            f"✅ Lamaran #{app['id']} terkirim ke {recipient}. Semoga lolos! 🍀\n\n"
            f"Balasan HR akan dicek otomatis, dan kalau {FOLLOWUP_DAYS} hari belum ada kabar "
            f"aku ingatkan untuk follow-up. Lihat semua lamaran di /lamaran.")

    elif action == "cancel":
        context.user_data["recipient"] = None
        await query.edit_message_reply_markup(None)
        await query.message.reply_text("Dibatalkan. Tidak ada email yang dikirim.")


# ================================================================ main
def main():
    if not ALLOWED:
        log.warning("ALLOWED_USER_IDS kosong: SIAPA PUN bisa memakai bot ini, dan cek harian tidak aktif!")

    persistence = PicklePersistence(filepath=str(DATA_DIR / "bot_state.pkl"))
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).persistence(persistence).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profil", profil))
    app.add_handler(CommandHandler("revisi", revisi))
    app.add_handler(CommandHandler(["lamaran", "riwayat"], lamaran))
    app.add_handler(CommandHandler("statistik", statistik))
    app.add_handler(CommandHandler("gaya", gaya))
    app.add_handler(CommandHandler("cekbalasan", cekbalasan))
    app.add_handler(CommandHandler("rekomendasi", rekomendasi))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
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
