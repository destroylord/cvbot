"""Penyesuaian CV per lowongan: metode PAR (tanya hasil), pratinjau skor, dan 🚀 Tingkatkan lagi.
"""
import asyncio
import difflib

import ai_helper
import cv_builder
import cv_fonts
import profile_edit as pe
from core import (
    cv_font_for, edit_profile, kb, load_user, log, safe_name,
    update_user, user_dir,
)
import menu_cv


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
        await menu_cv.finish_master_par(message, context, uid, par)
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
