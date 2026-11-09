"""Email lamaran dan pengiriman: revisi, konfirmasi, kirim, jadwal jam kerja, batas harian, antrean.
"""
import asyncio
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

import ai_helper
import gmail_accounts
import job_rules as rules
import mailer
import tracker
from core import (
    EMAIL_RE, FOLLOWUP_DAYS, GAYA_LABEL, GMAIL_MISSING, MAX_SEND_PER_DAY, TIMEZONE, default_account,
    ensure_master_cv, gmail_configured, kb, load_user, log, only_allowed,
    safe_edit, update_user,
)
import fitur_lamaran


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
        await msg.edit_text(**fitur_lamaran.followup_view(load_user(update.effective_user.id), followup))
    else:
        context.user_data["cover_letter"] = new_text
        await msg.edit_text(new_text, reply_markup=cover_letter_buttons())


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


async def receive_recipient(message, context, uid: int, text: str):
    """Alamat email HR yang diketik pengguna (lowongan tanpa email, atau ✏️ Ganti email tujuan)."""
    if not EMAIL_RE.match(text):
        await message.reply_text("Format email tidak valid, coba lagi.")
        return
    context.user_data["awaiting_email"] = False
    context.user_data["recipient"] = text
    await show_send_confirmation(message, context, uid)


async def btn_edit_recipient(query, context, uid, data, arg):
    context.user_data["awaiting_email"] = True
    await query.edit_message_reply_markup(None)
    await query.message.reply_text("Ketik alamat email tujuan yang benar:")


async def btn_cancel_send(query, context, uid, data, arg):
    context.user_data["recipient"] = None
    await query.edit_message_reply_markup(None)
    await query.message.reply_text("Dibatalkan. Tidak ada email yang dikirim.")
