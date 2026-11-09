"""🤖 Model AI: provider, model, model cadangan, API key, login Claude, tes AI.
"""
import asyncio
import time

from telegram.error import BadRequest

import ai_helper
import app_settings
from core import (
    ALLOWED, URL_RE, kb, log, safe_edit,
)


# Setting yang dipilih dari daftar model: kode di callback_data -> nama setting
MODEL_TARGETS = {
    "m": lambda: ai_helper.PROVIDERS[ai_helper.provider()]["model"],
    "f": lambda: ai_helper.PROVIDERS[ai_helper.provider()]["fallback"],
    "v": lambda: "OLLAMA_VISION_MODEL",
}


MODEL_TARGET_LABEL = {"m": "model utama", "f": "model cadangan", "v": "model untuk membaca foto"}


MAX_MODEL_BUTTONS = 30


CLAUDE_LOGIN_STEPS = (
    "Cara login (sekali saja, di VPS, sebagai user yang menjalankan bot):\n"
    "1. Install CLI ant dari https://github.com/anthropics/anthropic-cli/releases\n"
    "   (pilih file linux_amd64 atau linux_arm64, ekstrak, taruh ant di /usr/local/bin)\n"
    "2. Jalankan: ant auth login --no-browser\n"
    "   Buka link yang muncul, login ke akun Claude Console, lalu tempel kodenya ke terminal.\n"
    "3. Kalau ada baris ANTHROPIC_API_KEY di .env, hapus lalu ./bot.sh restart\n"
    "4. Tekan 🔄 Cek login.\n\n"
    "Catatan: ini login akun Claude Console (pemakaian API, bayar sesuai pemakaian), bukan kuota "
    "langganan Claude Pro/Max. Login kedaluwarsa berkala; kalau bot bilang login kedaluwarsa, ulangi langkah 2.")


def ai_view(note: str = ""):
    name = ai_helper.provider()
    info = ai_helper.PROVIDERS.get(name)
    if not info:
        return (f"{note}🤖 Model AI\n\nProvider '{name}' tidak dikenal. Pilih provider.",
                kb([("🔁 Pilih provider", "ai:prov")], [("⬅️ Pengaturan", "set:back")]))
    lines = [f"Provider: {info['label']}"]
    rows = [[("🔁 Ganti provider", "ai:prov")]]
    if name == "openai":
        lines.append(f"Alamat API: {ai_helper.setting('OPENAI_BASE_URL')}")
        rows.append([("🌐 Ganti alamat API", "ai:url")])
    lines.append(f"Model: {ai_helper.current_model() or 'belum dipilih'}")
    rows.append([("🧠 Pilih model", "ai:model")])
    if info["fallback"]:
        lines.append(f"Cadangan saat sibuk: {ai_helper.setting(info['fallback']) or 'tidak ada'}")
        rows.append([("🛟 Model cadangan", "ai:fb")])
    if name == "ollama":
        lines += [f"Alamat: {ai_helper.setting('OLLAMA_URL')}",
                  f"Model untuk foto: {ai_helper.setting('OLLAMA_VISION_MODEL')}"]
        rows.append([("🖼️ Model untuk foto", "ai:vis")])
    if name == "claude" and ai_helper.claude_uses_login():
        lines.append("Masuk dengan: login akun Claude Console (ant auth login)")
        rows.append([("🔄 Cek login", "ai:auth:login"), ("🔑 Pakai API key", "ai:auth:key")])
    elif info["key"]:
        key = ai_helper.setting(info["key"])
        lines.append(f"API key: {app_settings.masked(key) if key or name != 'openai' else 'tidak dipakai'}")
        rows.append([("🔑 Ganti API key", "ai:key")])
        if name == "claude":
            rows.append([("🔐 Pakai login (tanpa API key)", "ai:auth:login")])
    rows += [[("🧪 Tes AI", "ai:test")], [("⬅️ Pengaturan", "set:back")]]
    return f"{note}🤖 Model AI\n\n" + "\n".join(lines), kb(*rows)


def api_key_prompt(name: str):
    info = ai_helper.PROVIDERS[name]
    where = f"\nBuat di: {info['key_url']}" if info["key_url"] else ""
    text = (f"Kirim API key {name.capitalize() if name != 'openai' else 'untuk ' + ai_helper.setting('OPENAI_BASE_URL')}."
            f"{where}\n\nPesan berisi API key akan langsung aku hapus dari chat. Ketik batal untuk membatalkan.")
    markup = kb([("🚫 Tanpa API key (server lokal)", "ai:nokey")]) if name == "openai" else None
    return text, markup


async def ask_api_key(message, context, name: str):
    context.user_data["ai_form"] = {"step": "key", "provider": name}
    text, markup = api_key_prompt(name)
    await message.reply_text(text, reply_markup=markup)


def url_presets_view():
    rows = [[(label, f"ai:url:{i}")] for i, (label, _) in enumerate(ai_helper.OPENAI_PRESETS)]
    return ("Pilih layanan AI yang kompatibel dengan OpenAI, atau ketik alamat API lain:",
            kb(*rows, [("✏️ Alamat lain", "ai:url:custom")], [("⬅️ Kembali", "ai")]))


async def btn_ai(query, context, uid, data, arg):
    """ai, ai:prov[:<nama>], ai:url[:<i>|custom], ai:model|fb|vis, ai:set:<kode>:<i|none>, ai:type:<kode>,
    ai:key, ai:nokey, ai:auth:login|key, ai:test"""
    if not ALLOWED:
        await query.message.reply_text("Isi ALLOWED_USER_IDS di .env dulu supaya orang lain tidak bisa "
                                       "melihat atau mengubah pengaturan AI dan API key kamu.")
        return
    parts = arg.split(":") if arg else []
    cmd = parts[0] if parts else ""

    if cmd == "prov" and len(parts) == 1:
        current = ai_helper.provider()
        rows = [[(f"{'✅ ' if key == current else ''}{info['label']}", f"ai:prov:{key}")]
                for key, info in ai_helper.PROVIDERS.items()]
        await safe_edit(query.message, "Pilih provider AI:", kb(*rows, [("⬅️ Kembali", "ai")]))
        return
    if cmd == "prov":
        name = parts[1]
        if name not in ai_helper.PROVIDERS:
            return
        app_settings.set("AI_PROVIDER", name)
        note = f"✅ Provider diganti ke {ai_helper.PROVIDERS[name]['label']}.\n\n"
        if name == "openai":
            text, markup = url_presets_view()
            await safe_edit(query.message, note + text, markup)
            return
        if name == "claude" and not ai_helper.claude_uses_login() and not ai_helper.setting("ANTHROPIC_API_KEY"):
            await safe_edit(query.message, note + "Masuk ke Claude dengan cara apa?",
                            kb([("🔐 Login akun Claude Console", "ai:auth:login")],
                               [("🔑 API key", "ai:auth:key")]))
            return
        await safe_edit(query.message, *ai_view(note))
        key = ai_helper.PROVIDERS[name]["key"]
        if name != "claude" and key and not ai_helper.setting(key):
            await ask_api_key(query.message, context, name)
        return

    if cmd == "url" and len(parts) == 1:
        await safe_edit(query.message, *url_presets_view())
        return
    if cmd == "url" and parts[1] == "custom":
        context.user_data["ai_form"] = {"step": "url"}
        await query.message.reply_text("Ketik alamat API (base URL), contoh: https://openrouter.ai/api/v1 "
                                       "(atau ketik batal):")
        return
    if cmd == "url":
        label, url = ai_helper.OPENAI_PRESETS[int(parts[1])]
        app_settings.set("OPENAI_BASE_URL", url)
        await safe_edit(query.message, *ai_view(f"✅ Alamat API: {label}.\n\n"))
        await ask_api_key(query.message, context, "openai")
        return

    if cmd == "auth" and parts[1] == "key":
        app_settings.set("CLAUDE_AUTH", "key")
        await safe_edit(query.message, *ai_view())
        if not ai_helper.setting("ANTHROPIC_API_KEY"):
            await ask_api_key(query.message, context, "claude")
        return
    if cmd == "auth" and parts[1] == "login":
        app_settings.set("CLAUDE_AUTH", "login")
        await safe_edit(query.message, "⏳ Mengecek login Claude di VPS...")
        try:
            models = await asyncio.to_thread(ai_helper.list_models, "claude")
        except Exception as e:
            log.info("login Claude belum siap: %s", e)
            await safe_edit(query.message, f"❌ {e}\n\n{CLAUDE_LOGIN_STEPS}",
                            kb([("🔄 Cek login", "ai:auth:login")], [("🔑 Pakai API key saja", "ai:auth:key")],
                               [("⬅️ Kembali", "ai")]))
            return
        note = f"✅ Login Claude terdeteksi ({len(models)} model tersedia).\n"
        if models and ai_helper.current_model() not in models:
            note += (f"⚠️ Model sekarang ({ai_helper.current_model()}) tidak ada di daftar akun ini. "
                     f"Pilih lewat 🧠 Pilih model.\n")
        await safe_edit(query.message, *ai_view(note + "\n"))
        return

    if cmd in ("model", "fb", "vis"):
        code = {"model": "m", "fb": "f", "vis": "v"}[cmd]
        await safe_edit(query.message, "⏳ Mengambil daftar model dari provider...")
        try:
            models = await asyncio.to_thread(ai_helper.list_models, ai_helper.provider())
        except Exception as e:
            log.info("gagal mengambil daftar model: %s", e)
            await safe_edit(query.message, f"Gagal mengambil daftar model: {e}",
                            kb([("✏️ Ketik nama model", f"ai:type:{code}")], [("⬅️ Kembali", "ai")]))
            return
        total = len(models)
        models = models[:MAX_MODEL_BUTTONS]
        context.user_data["ai_models"] = models
        current = ai_helper.setting(MODEL_TARGETS[code]())
        rows = [[(f"{'✅ ' if m == current else ''}{m}", f"ai:set:{code}:{i}")] for i, m in enumerate(models)]
        if code == "f":
            rows.append([("🚫 Tanpa cadangan", "ai:set:f:none")])
        rows += [[("✏️ Ketik nama model", f"ai:type:{code}")], [("⬅️ Kembali", "ai")]]
        if not models:
            text = "Provider tidak mengembalikan model apa pun. Ketik nama model secara manual."
        elif total > len(models):
            text = (f"Pilih {MODEL_TARGET_LABEL[code]} ({total} model tersedia, {len(models)} pertama ditampilkan; "
                    f"model gratis di atas). Model lain bisa diketik lewat ✏️ Ketik nama model:")
        else:
            text = f"Pilih {MODEL_TARGET_LABEL[code]} ({total} model tersedia untuk akun kamu):"
        await safe_edit(query.message, text, kb(*rows))
        return

    if cmd == "set":
        code, choice = parts[1], parts[2]
        models = context.user_data.get("ai_models") or []
        if choice != "none" and int(choice) >= len(models):
            await safe_edit(query.message, *ai_view("Daftar model sudah kedaluwarsa, pilih lagi.\n\n"))
            return
        value = "" if choice == "none" else models[int(choice)]
        app_settings.set(MODEL_TARGETS[code](), value)
        await safe_edit(query.message, *ai_view(
            f"✅ {MODEL_TARGET_LABEL[code].capitalize()}: {value or 'tidak ada'}. Tekan 🧪 Tes AI untuk mencoba.\n\n"))
        return

    if cmd == "type":
        context.user_data["ai_form"] = {"step": "model", "target": parts[1]}
        hint = " Untuk beberapa model cadangan, pisahkan dengan koma." if parts[1] == "f" else ""
        await query.message.reply_text(f"Ketik nama {MODEL_TARGET_LABEL[parts[1]]}.{hint} (atau ketik batal):")
        return

    if cmd == "key":
        await ask_api_key(query.message, context, ai_helper.provider())
        return
    if cmd == "nokey":
        app_settings.set("OPENAI_API_KEY", "")
        context.user_data["ai_form"] = None
        await safe_edit(query.message, *ai_view("✅ Tanpa API key. Sekarang pilih model lewat 🧠 Pilih model.\n\n"))
        return

    if cmd == "test":
        await safe_edit(query.message, f"⏳ Mengetes {ai_helper.provider()} · {ai_helper.current_model()}...")
        started = time.monotonic()
        try:
            answer = await asyncio.to_thread(ai_helper.ping)
            note = f"✅ AI menjawab dalam {time.monotonic() - started:.1f} detik: \"{answer[:40]}\"\n\n"
        except Exception as e:
            log.exception("tes AI gagal (%s · %s)", ai_helper.provider(), ai_helper.current_model())
            note = f"❌ Tes gagal: {e}\n\n"
        await safe_edit(query.message, *ai_view(note))
        return

    await safe_edit(query.message, *ai_view())


async def handle_ai_form(message, context, uid: int, text: str):
    """Isian alamat API / API key / nama model yang diketik manual."""
    form = context.user_data["ai_form"]
    if text.lower() == "batal":
        context.user_data["ai_form"] = None
        await message.reply_text("Dibatalkan.")
        return

    if form["step"] == "url":
        url = text.strip().rstrip("/")
        if not URL_RE.fullmatch(url):
            await message.reply_text("Alamat harus diawali http:// atau https://. Coba lagi (atau ketik batal).")
            return
        app_settings.set("OPENAI_BASE_URL", url)
        await ask_api_key(message, context, "openai")
        return

    if form["step"] == "model":
        context.user_data["ai_form"] = None
        app_settings.set(MODEL_TARGETS[form["target"]](), text.strip())
        reply, markup = ai_view(f"✅ {MODEL_TARGET_LABEL[form['target']].capitalize()}: {text.strip()}. "
                                f"Tekan 🧪 Tes AI untuk mencoba.\n\n")
        await message.reply_text(reply, reply_markup=markup)
        return

    # langkah API key: hapus pesannya dari chat, lalu kirim pesan baru (bukan reply)
    try:
        await message.delete()
    except BadRequest:
        log.warning("pesan berisi API key tidak bisa dihapus")
    provider, key = form["provider"], text.strip()
    msg = await context.bot.send_message(message.chat_id, "⏳ Memeriksa API key...")
    note = ""
    try:
        models = await asyncio.to_thread(ai_helper.list_models, provider, key)
    except ai_helper.NoModelList as e:
        models, note = [], f"ℹ️ Key disimpan tanpa diperiksa: {e}\n"
    except Exception as e:
        log.info("API key %s ditolak: %s", provider, e)
        await msg.edit_text(f"❌ {e}\nKirim API key lagi, atau ketik batal.")
        return
    app_settings.set(ai_helper.PROVIDERS[provider]["key"], key)
    if provider == "claude":
        app_settings.set("CLAUDE_AUTH", "key")
    context.user_data["ai_form"] = None
    note = (note or f"✅ API key disimpan ({len(models)} model tersedia).\n")
    if ai_helper.provider() == provider and models and ai_helper.current_model() not in models:
        note += (f"⚠️ Model sekarang ({ai_helper.current_model() or 'belum dipilih'}) tidak ada di daftar. "
                 f"Pilih lewat 🧠 Pilih model.\n")
    reply, markup = ai_view(note + "\n")
    await msg.edit_text(reply, reply_markup=markup)
