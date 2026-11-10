"""Bot Telegram CV: pintu masuk. Mendaftarkan perintah, tombol, dan pesan, lalu menjalankan bot.

Setiap fitur ada di modulnya sendiri (fitur_*.py, menu_*.py); bagian bersama di core.py.
File ini hanya meneruskan pesan ke fitur yang tepat.
"""
import datetime as dt
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, PicklePersistence, filters,
)

from core import (
    ALLOWED, DAILY_CHECK_TIME, DATA_DIR, IMAGE_EXT, MAX_IMAGE_BYTES, REPLY_CHECK_MINUTES, TAILOR_THRESHOLD,
    TIMEZONE, load_user, log, main_menu, only_allowed,
)
import fitur_backup
import fitur_interview
import fitur_kirim
import fitur_lamaran
import fitur_lowongan
import fitur_penyesuaian
import fitur_rekomendasi
import menu_ai
import menu_akun
import menu_cv
import menu_pengaturan


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
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """File: gambar lowongan, file backup (.zip), atau CV (PDF/DOCX)."""
    message, uid = update.message, update.effective_user.id
    doc = message.document
    ext = Path(doc.file_name or "").suffix.lower()
    if ext in IMAGE_EXT:
        if (doc.file_size or 0) > MAX_IMAGE_BYTES:
            await message.reply_text("Gambarnya terlalu besar. Kirim sebagai foto biasa (bukan file) ya.")
            return
        await fitur_lowongan.process_job_image(message, context, uid, doc, IMAGE_EXT[ext])
    elif ext == ".zip":
        await fitur_backup.receive_backup(message, context, uid, doc)
    elif ext in (".pdf", ".docx"):
        await menu_cv.receive_cv(message, context, uid, doc, ext)
    else:
        await message.reply_text("Kirim CV dalam format PDF/DOCX, gambar lowongan (JPG/PNG), atau file backup (.zip).")


# Tombol menu di bawah kolom chat
MENU_ACTIONS = {
    "🎯 Rekomendasi": fitur_rekomendasi.rekomendasi,
    "📂 Lamaran": fitur_lamaran.lamaran,
    "📬 Cek Balasan": fitur_lamaran.cekbalasan,
    "📊 Statistik": fitur_lamaran.statistik,
    "📄 CV Saya": menu_cv.profil,
    "👤 Profil": menu_cv.profil,  # label lama, masih ada di keyboard pengguna sebelum /start ulang
    "⚙️ Pengaturan": menu_pengaturan.pengaturan,
}

# Isian yang sedang menunggu ketikan pengguna: (kunci di user_data, penanganan). Dicek berurutan.
TEXT_FORMS = [
    ("gmail_form", menu_akun.handle_gmail_form),
    ("ai_form", menu_ai.handle_ai_form),
    ("font_form", menu_pengaturan.handle_font_form),
    ("cv_form", menu_cv.handle_cv_form),
    ("par", fitur_penyesuaian.handle_par_answer),
    ("gap", fitur_penyesuaian.handle_gap_story),
    ("pref_form", menu_pengaturan.handle_pref_form),
    ("iv", fitur_interview.handle_iv_answer),
]
CLEARED_BY_MENU = {"awaiting_email": False, "gmail_form": None, "ai_form": None, "font_form": False, "par": None,
                   "cv_form": None, "gap": None, "pref_form": None, "iv": None}


def _waiting_for_text(context, key: str) -> bool:
    value = context.user_data.get(key)
    if key == "gap":  # sesi 🚀 Tingkatkan lagi hanya menunggu teks setelah pengguna menekan "Pernah"
        return bool(value and value.get("awaiting_story"))
    return bool(value)


@only_allowed
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text in MENU_ACTIONS:  # tombol menu selalu menang dan membatalkan isian yang sedang berjalan
        context.user_data.update(CLEARED_BY_MENU)
        await MENU_ACTIONS[text](update, context)
        return
    for key, handler in TEXT_FORMS:
        if _waiting_for_text(context, key):
            await handler(update.message, context, uid, text)
            return
    if context.user_data.get("awaiting_email"):
        await fitur_kirim.receive_recipient(update.message, context, uid, text)
        return
    await fitur_lowongan.receive_job_text(update.message, context, uid, text)


# Tombol inline: callback_data "<nama>" atau "<nama>:<argumen>" -> fungsi(query, context, uid, data, arg)
BUTTONS = {
    # lowongan & penyesuaian CV
    "skip": fitur_lowongan.btn_skip, "retryjob": fitur_lowongan.btn_retry_job,
    "rec": fitur_rekomendasi.btn_recommendation,
    "tailor": fitur_penyesuaian.btn_tailor, "orig": fitur_penyesuaian.btn_original_cv,
    "par": fitur_penyesuaian.btn_par, "tl": fitur_penyesuaian.btn_tailor_result, "gap": fitur_penyesuaian.btn_gap,
    # email lamaran & kirim
    "cl": fitur_kirim.btn_cover_letter, "send": fitur_kirim.btn_send, "confirm": fitur_kirim.btn_confirm_send,
    "editto": fitur_kirim.btn_edit_recipient, "cancel": fitur_kirim.btn_cancel_send, "q": fitur_kirim.btn_queue,
    # 📂 lamaran & interview
    "lamlist": fitur_lamaran.btn_lamaran_list, "app": fitur_lamaran.btn_app_detail, "st": fitur_lamaran.btn_status,
    "str": lambda *a: fitur_lamaran.btn_status(*a, new_message=True),
    "fu": fitur_lamaran.btn_followup_draft, "fusend": fitur_lamaran.btn_followup_send,
    "fucancel": fitur_lamaran.btn_followup_cancel, "iv": fitur_interview.btn_iv,
    # menu
    "cv": menu_cv.btn_cv, "set": menu_pengaturan.btn_settings, "gaya": menu_pengaturan.btn_gaya,
    "font": menu_pengaturan.btn_font, "pref": menu_pengaturan.btn_pref, "acc": menu_akun.btn_accounts,
    "ai": menu_ai.btn_ai, "bk": fitur_backup.btn_backup,
}
NEEDS_JOB = {"tailor", "orig", "cl", "send", "confirm", "editto", "cancel", "tl"}  # butuh lowongan yang sedang dibuka


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


async def post_init(app: Application):
    # Daftar perintah di tombol Menu hanya tampilan: kalau gagal (jaringan lambat), bot tetap jalan
    try:
        await app.bot.set_my_commands([BotCommand(name, desc) for name, desc in COMMANDS])
    except TelegramError as e:
        log.warning("daftar perintah Menu belum terpasang (%s), dicoba lagi saat bot start berikutnya", e)


def main():
    if not ALLOWED:
        log.warning("ALLOWED_USER_IDS kosong: SIAPA PUN bisa memakai bot ini, dan cek harian tidak aktif!")

    persistence = PicklePersistence(filepath=str(DATA_DIR / "bot_state.pkl"))
    # Batas tunggu bawaan (5 detik) terlalu pendek untuk koneksi VPS yang kadang lambat ke api.telegram.org
    app = (Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).persistence(persistence)
           .connect_timeout(20).read_timeout(30).write_timeout(30).pool_timeout(10)
           .post_init(post_init).build())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["cv", "profil"], menu_cv.profil))
    app.add_handler(CommandHandler("revisi", fitur_kirim.revisi))
    app.add_handler(CommandHandler(["lamaran", "riwayat"], fitur_lamaran.lamaran))
    app.add_handler(CommandHandler("statistik", fitur_lamaran.statistik))
    app.add_handler(CommandHandler("gaya", menu_pengaturan.gaya))
    app.add_handler(CommandHandler(["pengaturan", "config"], menu_pengaturan.pengaturan))
    app.add_handler(CommandHandler("cekbalasan", fitur_lamaran.cekbalasan))
    app.add_handler(CommandHandler("rekomendasi", fitur_rekomendasi.rekomendasi))
    app.add_handler(CommandHandler("backup", fitur_backup.backup))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, fitur_lowongan.handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_button))

    if app.job_queue and ALLOWED:
        hour, minute = (int(x) for x in DAILY_CHECK_TIME.split(":"))
        when = dt.time(hour, minute, tzinfo=ZoneInfo(TIMEZONE))
        for uid in ALLOWED:
            app.job_queue.run_daily(fitur_rekomendasi.daily_check, when, chat_id=uid, user_id=uid, name=f"daily_{uid}")
            app.job_queue.run_repeating(fitur_lamaran.reply_check_job, interval=REPLY_CHECK_MINUTES * 60, first=60,
                                        chat_id=uid, user_id=uid, name=f"replies_{uid}")
        log.info("Rekomendasi & pengingat harian jam %s (%s), cek balasan HR tiap %s menit",
                 DAILY_CHECK_TIME, TIMEZONE, REPLY_CHECK_MINUTES)
        for uid in ALLOWED:  # lamaran terjadwal yang belum terkirim (misalnya setelah bot restart)
            for item in load_user(uid).get("antrian") or []:
                fitur_kirim.schedule_queued(app.job_queue, uid, item)

    log.info("Bot berjalan...")
    # bootstrap_retries=-1: kalau jaringan putus saat start, terus coba lagi (bawaannya langsung berhenti)
    app.run_polling(bootstrap_retries=-1)


if __name__ == "__main__":
    main()
