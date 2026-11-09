"""🎯 Rekomendasi dari email job alert dan cek otomatis harian.
"""
import asyncio
import hashlib

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import ai_helper
import gmail_accounts
import job_alerts
import job_fetcher
import job_rules as rules
from core import (
    GMAIL_MISSING, LOOKBACK_DAYS, MAX_RECS, MIN_REC_SCORE, finish_progress, gmail_configured,
    load_user, log, only_allowed, update_user,
)
import fitur_lamaran
import fitur_lowongan


@only_allowed
async def rekomendasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_recommendations(context, update.effective_user.id, update.effective_chat.id)


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
        await fitur_lamaran.send_followup_reminders(context, uid, chat_id)
    except Exception:
        log.exception("pengingat follow-up gagal")


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
    source = fitur_lowongan.source_from_link(rec.get("link"))
    await fitur_lowongan.process_job(query.message, context, uid, job_text, source)
