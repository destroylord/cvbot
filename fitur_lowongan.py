"""Lowongan masuk (teks, link, foto): analisis, ringkasan, dan peringatan.
"""
import asyncio
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import ContextTypes

import ai_helper
import job_fetcher
import job_rules as rules
import tracker
from core import (
    TAILOR_THRESHOLD, TIMEZONE, URL_RE, kb, load_user, log, only_allowed,
)
import fitur_penyesuaian


async def receive_job_text(message, context, uid: int, text: str):
    """Pesan teks biasa: link lowongan (dibuka dulu) atau deskripsi lowongan yang di-paste/forward."""
    url = URL_RE.search(text)
    if url:
        msg = await message.reply_text("⏳ Membuka link lowongan...")
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
        await message.reply_text("Teksnya terlalu pendek. Kirim link lowongan atau paste deskripsi lengkapnya.")
        return
    await process_job(message, context, uid, job_text, source_from_link(url.group(0)) if url else None)


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
        await fitur_penyesuaian.tailor_flow(message, context, uid)
    else:
        await msg.edit_text(job_summary(job), reply_markup=kb(
            [("✍️ Buat Email Lamaran", "cl"), ("🛠️ Sesuaikan CV", "tailor")],
            [("❌ Lewati", "skip")]))


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


# Setiap tombol punya callback_data "<nama>" atau "<nama>:<argumen>". Satu fungsi per tombol,
# dipetakan di BUTTONS. Semua fungsi menerima (query, context, uid, data, arg).
async def btn_skip(query, context, uid, data, arg):
    await query.edit_message_reply_markup(None)
    await query.message.reply_text("Oke, dilewati. Kirim lowongan berikutnya kapan saja.")


async def btn_retry_job(query, context, uid, data, arg):
    job_text = context.user_data.get("retry_job_text")
    await query.edit_message_reply_markup(None)
    if not job_text:
        await query.message.reply_text("Lowongan ini sudah tidak tersimpan. Kirim ulang lowongannya ya.")
        return
    await process_job(query.message, context, uid, job_text, context.user_data.get("retry_job_source"))
