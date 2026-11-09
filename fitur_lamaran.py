"""📂 Lamaran: daftar & status, cek balasan HR, pengingat dan email follow-up, statistik.
"""
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

import ai_helper
import gmail_accounts
import gmail_replies
import mailer
import tracker
from core import (
    FOLLOWUP_DAYS, GMAIL_MISSING, JENIS_LABEL, LOUD_REPLIES, TIMEZONE, finish_progress,
    gmail_configured, kb, load_user, log, only_allowed, safe_edit,
    update_user,
)
import fitur_kirim


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


@only_allowed
async def cekbalasan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_replies(context, update.effective_user.id, update.effective_chat.id, manual=True)


async def reply_check_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if load_user(context.job.user_id).get("auto_balasan", True):
            await check_replies(context, context.job.user_id, context.job.chat_id)
    except Exception:
        log.exception("cek balasan HR gagal")


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
    subject = app.get("subjek") or fitur_kirim.email_subject(app, (data.get("profile") or {}).get("nama") or "")
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
