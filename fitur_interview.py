"""🎤 Persiapan interview dan latihan tanya-jawab.
"""
import asyncio

import ai_helper
import tracker
from core import (
    kb, load_user, log, update_user,
)


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
