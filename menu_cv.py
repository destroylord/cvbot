"""📄 CV Saya: lihat & ubah CV asli, unduh PDF, dan ✨ Perbaiki poin dengan PAR.
"""
import asyncio
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

import ai_helper
import cv_fonts
import cv_parser
import profile_edit as pe
from core import (
    edit_profile, ensure_master_cv, kb, load_user, log, only_allowed,
    safe_edit, safe_name, save_user, user_dir,
)
import fitur_penyesuaian


@only_allowed
async def profil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_user(update.effective_user.id)
    if not data.get("profile"):
        await update.message.reply_text("Belum ada CV. Kirim dulu file PDF/DOCX kamu.")
        return
    text, markup = cv_overview(data)
    await update.message.reply_text(text, reply_markup=markup)


async def receive_cv(message, context, uid: int, doc, ext: str):
    """CV baru (PDF/DOCX): baca teksnya, ringkas jadi profil dengan AI, deteksi fontnya."""
    original_dir = user_dir(uid) / "original"
    original_dir.mkdir(exist_ok=True)
    for old in original_dir.iterdir():
        old.unlink()
    path = original_dir / f"{safe_name(Path(doc.file_name).stem, 60)}{ext}"

    msg = await message.reply_text("⏳ Membaca CV...")
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


# ---- 📄 CV Saya: ubah CV asli
CV_STALE = "Data CV sudah berubah, buka lagi dari 📄 CV Saya."


CV_FORM_HINT = "\n\nKetik batal untuk membatalkan."


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
    questions = fitur_penyesuaian.par_questions(data, profile, result)
    if questions:
        context.user_data["par"] = {"mode": "master", "questions": questions, "i": 0, "answers": [], "result": result}
        await msg.delete()
        await fitur_penyesuaian.ask_par_question(message, context)
        return
    await show_master_preview(msg, message, context, uid, result)


async def finish_master_par(message, context, uid: int, par: dict):
    answers, result = par["answers"], par["result"]
    if not answers:
        msg = await message.reply_text("⏳ Menyiapkan pratinjau...")
        await show_master_preview(msg, message, context, uid, result)
        return
    data = fitur_penyesuaian.save_facts(uid, answers)
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
