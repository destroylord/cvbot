"""⚙️ Pengaturan: ringkasan & sakelar, gaya email lamaran, 🔤 font CV, 🎯 preferensi loker.
"""
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

import ai_helper
import cv_fonts
import gmail_accounts
import job_rules as rules
from core import (
    DAILY_CHECK_TIME, GAYA_LABEL, MAX_SEND_PER_DAY, REPLY_CHECK_MINUTES, cv_font_for, default_account,
    kb, load_user, only_allowed, safe_edit, update_user,
)


def gaya_view(data: dict):
    current = data.get("gaya", "formal")
    return (f"Gaya email lamaran sekarang: {GAYA_LABEL.get(current)}\n\n"
            "Isi email tidak mengulang isi CV (CV sudah dilampirkan).\n\n"
            "🎩 Formal: sopan dan profesional, 120-180 kata\n"
            "😊 Santai: hangat tapi tetap sopan, 90-140 kata\n"
            "⚡ Singkat: langsung ke inti, 50-80 kata",
            kb([(label, f"gaya:{key}") for key, label in GAYA_LABEL.items()]))


@only_allowed
async def gaya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = gaya_view(load_user(update.effective_user.id))
    await update.message.reply_text(text, reply_markup=markup)


TOGGLES = {"auto_rekomendasi": "🎯 Rekomendasi harian", "auto_balasan": "📬 Cek balasan otomatis",
           "jadwal_kirim": "🕗 Kirim di jam kerja"}


def settings_view(data: dict):
    accounts = gmail_accounts.load()
    default = default_account(data)
    onoff = {key: "Aktif" if data.get(key, True) else "Mati" for key in TOGGLES}
    ai = ai_helper.PROVIDERS.get(ai_helper.provider(), {})
    text = (f"⚙️ Pengaturan\n\n"
            f"AI: {ai.get('label', ai_helper.provider()).split(' (')[0]} · {ai_helper.current_model() or '-'}\n"
            f"Gaya email lamaran: {GAYA_LABEL.get(data.get('gaya', 'formal'))}\n"
            f"Font CV: {cv_fonts.label(cv_font_for(data))}"
            f"{' (sama seperti CV asli)' if data.get('font_cv', 'auto') == 'auto' else ''}\n"
            f"Akun Gmail default: {default.address if default else 'belum ada'}"
            f"{f' ({len(accounts)} akun terhubung)' if len(accounts) > 1 else ''}\n"
            f"Rekomendasi harian jam {DAILY_CHECK_TIME}: {onoff['auto_rekomendasi']}\n"
            f"Cek balasan HR tiap {REPLY_CHECK_MINUTES} menit: {onoff['auto_balasan']}\n"
            f"Kirim lamaran hanya di jam kerja (Senin-Jumat {rules.WORK_START:02d}.00-{rules.WORK_END:02d}.00): "
            f"{onoff['jadwal_kirim']}\n"
            f"Batas kirim: {MAX_SEND_PER_DAY} lamaran/hari per akun Gmail\n\n"
            f"Jam cek, ambang skor, dan batas kirim diatur di file .env.")
    rows = [[("🤖 Model AI", "ai")], [("✍️ Gaya email lamaran", "set:gaya"), ("🔤 Font CV", "font")],
            [("📧 Akun Gmail", "acc"), ("🎯 Preferensi loker", "pref")], [("💾 Backup data", "bk")]]
    rows += [[(f"{label}: {'ON ✅' if data.get(key, True) else 'OFF ⛔'}", f"set:tog:{key}")]
             for key, label in TOGGLES.items()]
    return text, kb(*rows)


@only_allowed
async def pengaturan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = settings_view(load_user(update.effective_user.id))
    await update.message.reply_text(text, reply_markup=markup)


async def btn_gaya(query, context, uid, data, style):
    update_user(uid, lambda d: d.__setitem__("gaya", style))
    markup = kb([("🔄 Buat ulang email lamaran", "cl")]) if context.user_data.get("job") else None
    await safe_edit(query.message, f"✅ Gaya email lamaran diubah ke {GAYA_LABEL.get(style)}.", markup)


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


def font_view(data: dict, note: str = ""):
    choice = data.get("font_cv", "auto")
    raw = data.get("font_asli")
    auto_family = cv_fonts.resolve(raw)
    text = (f"{note}🔤 Font CV yang disesuaikan\n\n"
            f"Font CV asli kamu: {cv_fonts.clean_name(raw) if raw else 'tidak terdeteksi'}\n"
            f"Dipakai sekarang: {cv_fonts.label(cv_font_for(data))}\n\n"
            "Font Microsoft (Calibri, Arial, Times New Roman, Cambria, Georgia) diganti padanannya yang "
            "ukurannya identik, karena font Microsoft tidak boleh disebarkan bebas.")
    rows = [[(f"{'✅ ' if choice == 'auto' else ''}📄 Sama seperti CV asli ({cv_fonts.label(auto_family)})",
              "font:auto")]]
    rows += [[(f"{'✅ ' if choice == fam else ''}{label}", f"font:{i}")] for i, (fam, label) in enumerate(cv_fonts.CHOICES)]
    rows += [[("✏️ Font lain dari Google Fonts", "font:type")], [("⬅️ Pengaturan", "set:back")]]
    return text, kb(*rows)


async def btn_font(query, context, uid, data, arg):
    """font, font:auto, font:<i>, font:type"""
    if arg == "type":
        context.user_data["font_form"] = True
        await query.message.reply_text("Ketik nama font persis seperti di fonts.google.com, contoh: Raleway, Inter, "
                                       "Nunito, Source Sans 3 (atau ketik batal):")
        return
    if arg == "auto" or arg.isdigit():
        value = "auto" if arg == "auto" else cv_fonts.CHOICES[int(arg)][0]
        data = update_user(uid, lambda d: d.__setitem__("font_cv", value) or d)
        text, markup = font_view(data, "✅ Font disimpan, berlaku untuk CV berikutnya.\n\n")
    else:
        text, markup = font_view(data)
    await safe_edit(query.message, text, markup)


async def handle_font_form(message, context, uid: int, text: str):
    if text.lower() == "batal":
        context.user_data["font_form"] = False
        await message.reply_text("Dibatalkan.")
        return
    family = " ".join(text.split())
    msg = await message.reply_text(f"⏳ Mengambil font {family} dari Google Fonts...")
    _, _, ok = await asyncio.to_thread(cv_fonts.ensure, family)
    if not ok:
        await msg.edit_text(f"Font \"{family}\" tidak ditemukan di Google Fonts (atau VPS tidak bisa mengaksesnya). "
                            "Cek ejaannya di fonts.google.com lalu kirim lagi, atau ketik batal.")
        return
    context.user_data["font_form"] = False
    data = update_user(uid, lambda d: d.__setitem__("font_cv", family) or d)
    reply, markup = font_view(data, f"✅ Font {family} disimpan, berlaku untuk CV berikutnya.\n\n")
    await msg.edit_text(reply, reply_markup=markup)


# ---- 🎯 Preferensi loker
def pref_view(data: dict, note: str = ""):
    prefs = data.get("pref") or {}
    mode = prefs.get("mode") or "semua"
    text = (f"{note}🎯 Preferensi loker\n\n"
            f"📍 Lokasi: {', '.join(prefs.get('lokasi') or []) or 'semua'}\n"
            f"🏠 Mode kerja: {rules.MODES[mode]}\n"
            f"💰 Gaji minimal: {rules.rupiah(prefs['gaji_min']) if prefs.get('gaji_min') else '-'}\n"
            f"🚫 Hindari posisi: {', '.join(prefs.get('hindari') or []) or '-'}\n\n"
            "Lowongan yang tidak sesuai disaring dari 🎯 Rekomendasi, dan diberi peringatan kalau kamu kirim manual. "
            "Info yang tidak disebut di lowongan tidak dianggap masalah.")
    modes = [(f"{'✅ ' if key == mode else ''}{label}", f"pref:mode:{key}") for key, label in rules.MODES.items()]
    return text, kb([("📍 Lokasi", "pref:lok"), ("💰 Gaji minimal", "pref:gaji")], modes,
                    [("🚫 Posisi yang dihindari", "pref:hindari")], [("🔄 Reset", "pref:reset"), ("⬅️ Pengaturan", "set:back")])


PREF_PROMPTS = {
    "lok": "Ketik kota yang kamu mau, pisahkan koma (contoh: Malang, Surabaya, Jakarta). Lowongan remote selalu lolos.",
    "gaji": "Ketik gaji minimal per bulan (contoh: 5jt atau 5.000.000).",
    "hindari": "Ketik kata di judul posisi yang mau kamu hindari, pisahkan koma (contoh: sales, telemarketing).",
}


async def btn_pref(query, context, uid, data, arg):
    """pref, pref:lok|gaji|hindari, pref:mode:<x>, pref:reset"""
    parts = arg.split(":") if arg else [""]
    if parts[0] in PREF_PROMPTS:
        context.user_data["pref_form"] = parts[0]
        await query.message.reply_text(PREF_PROMPTS[parts[0]] + "\n\nKetik - untuk mengosongkan, atau batal.")
        return
    note = ""
    if parts[0] == "mode" and parts[1] in rules.MODES:
        data = update_user(uid, lambda d: d.setdefault("pref", {}).__setitem__("mode", parts[1]) or d)
        note = "✅ Tersimpan.\n\n"
    elif parts[0] == "reset":
        data = update_user(uid, lambda d: d.__setitem__("pref", {}) or d)
        note = "✅ Preferensi dikosongkan.\n\n"
    await safe_edit(query.message, *pref_view(data, note))


async def handle_pref_form(message, context, uid: int, text: str):
    field = context.user_data["pref_form"]
    if text.lower() == "batal":
        context.user_data["pref_form"] = None
        await message.reply_text("Dibatalkan.")
        return
    value = text.strip()
    if field == "gaji":
        amount = None if value == "-" else rules.parse_salary(value)
        if value != "-" and not amount:
            await message.reply_text("Tidak terbaca. Contoh: 5jt atau 5.000.000. Coba lagi, atau ketik batal.")
            return
        stored = amount
        key = "gaji_min"
    else:
        stored = [] if value == "-" else [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]
        key = "lokasi" if field == "lok" else "hindari"
    context.user_data["pref_form"] = None
    data = update_user(uid, lambda d: d.setdefault("pref", {}).__setitem__(key, stored) or d)
    reply, markup = pref_view(data, "✅ Tersimpan.\n\n")
    await message.reply_text(reply, reply_markup=markup)
