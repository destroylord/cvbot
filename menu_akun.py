"""📧 Akun Gmail: tambah, ganti app password, hapus, jadikan default.
"""
import asyncio

from telegram.error import BadRequest

import gmail_accounts
from core import (
    ALLOWED, APP_PASSWORD_RE, EMAIL_RE, default_account, kb, load_user,
    log, safe_edit, update_user,
)


def accounts_view(data: dict):
    accounts = gmail_accounts.load()
    default = default_account(data)
    if accounts:
        lines = [f"{'⭐' if a == default else '•'} {a.address}{' (.env)' if a.from_env else ''}" for a in accounts]
        text = ("📧 Akun Gmail\n\n" + "\n".join(lines) +
                "\n\n⭐ = akun default saat mengirim lamaran. Job alert dibaca dari semua akun.\n"
                "Pilih akun untuk jadikan default, ganti app password, atau hapus.")
    else:
        text = "📧 Akun Gmail\n\nBelum ada akun. Tambahkan akun untuk membaca job alert dan mengirim lamaran."
    rows = [[(a.address, f"acc:{i}")] for i, a in enumerate(accounts)]
    return text, kb(*rows, [("➕ Tambah akun", "acc:add")], [("⬅️ Pengaturan", "set:back")])


def account_detail_view(data: dict, i: int, account: gmail_accounts.Account):
    is_default = account == default_account(data)
    text = (f"📧 {account.address}\n"
            f"{'⭐ Akun default' if is_default else 'Bukan akun default'}\n")
    rows = [] if is_default else [[("⭐ Jadikan default", f"acc:{i}:def")]]
    if account.from_env:
        text += "\nAkun ini diatur di file .env, jadi app password dan penghapusannya diubah lewat .env."
    else:
        rows += [[("🔑 Ganti app password", f"acc:{i}:pw")], [("🗑️ Hapus akun", f"acc:{i}:del")]]
    return text, kb(*rows, [("⬅️ Daftar akun", "acc")])


ADD_ACCOUNT_HELP = (
    "Ketik app password Gmail untuk {address} (16 huruf, spasi boleh).\n\n"
    "Cara buat: aktifkan verifikasi 2 langkah di akun Google, lalu buka "
    "https://myaccount.google.com/apppasswords dan buat app password baru.\n\n"
    "Pesan berisi password akan langsung aku hapus dari chat. Ketik batal untuk membatalkan.")


async def btn_accounts(query, context, uid, data, arg):
    """acc, acc:add, acc:<i>, acc:<i>:def, acc:<i>:pw, acc:<i>:del, acc:delok"""
    if not ALLOWED:
        await query.message.reply_text("Isi ALLOWED_USER_IDS di .env dulu supaya orang lain tidak bisa "
                                       "melihat atau mengubah akun Gmail kamu.")
        return
    parts = arg.split(":") if arg else []
    if not parts:
        text, markup = accounts_view(data)
        await safe_edit(query.message, text, markup)
        return
    if parts[0] == "add":
        context.user_data["gmail_form"] = {"step": "address"}
        await query.message.reply_text("Ketik alamat Gmail yang mau ditambahkan (atau ketik batal):")
        return
    if parts[0] == "delok":
        address = context.user_data.pop("gmail_delete", None)
        if address:
            gmail_accounts.remove(address)
            await query.message.reply_text(f"🗑️ {address} dihapus.")
        text, markup = accounts_view(load_user(uid))
        await safe_edit(query.message, text, markup)
        return

    accounts = gmail_accounts.load()
    i = int(parts[0])
    if i >= len(accounts):
        text, markup = accounts_view(data)
        await safe_edit(query.message, "Daftar akun sudah berubah, pilih lagi.\n\n" + text, markup)
        return
    account = accounts[i]
    action = parts[1] if len(parts) > 1 else ""
    if action == "def":
        update_user(uid, lambda d: d.__setitem__("gmail_default", account.address))
        data = load_user(uid)
    elif action == "pw" and not account.from_env:
        context.user_data["gmail_form"] = {"step": "password", "address": account.address, "edit": True}
        await query.message.reply_text(ADD_ACCOUNT_HELP.format(address=account.address))
        return
    elif action == "del" and not account.from_env:
        context.user_data["gmail_delete"] = account.address
        await safe_edit(query.message,
                        f"Hapus {account.address} dari bot?\n\nLamaran yang dikirim dari akun ini tidak "
                        f"bisa dicek balasannya atau di-follow-up sampai akunnya ditambahkan lagi.",
                        kb([("🗑️ Ya, hapus", "acc:delok"), ("❌ Batal", f"acc:{i}")]))
        return
    text, markup = account_detail_view(data, i, account)
    await safe_edit(query.message, text, markup)


async def handle_gmail_form(message, context, uid: int, text: str):
    """Isian tambah akun / ganti app password, satu langkah per pesan."""
    form = context.user_data["gmail_form"]
    if text.lower() == "batal":
        context.user_data["gmail_form"] = None
        await message.reply_text("Dibatalkan.")
        return

    if form["step"] == "address":
        address = text.lower()
        if not EMAIL_RE.match(address):
            await message.reply_text("Format email tidak valid, coba lagi (atau ketik batal).")
            return
        if gmail_accounts.get(address):
            await message.reply_text(f"{address} sudah terdaftar. Pilih akunnya di 📧 Akun Gmail untuk "
                                     "mengganti app password.")
            context.user_data["gmail_form"] = None
            return
        form.update({"step": "password", "address": address, "edit": False})
        await message.reply_text(ADD_ACCOUNT_HELP.format(address=address))
        return

    # langkah password: hapus pesannya dari chat secepatnya. Setelah itu kirim pesan baru,
    # bukan reply, karena pesan yang dibalas sudah tidak ada.
    password = text.replace(" ", "")
    try:
        await message.delete()
    except BadRequest:
        log.warning("pesan berisi app password tidak bisa dihapus")
    if not APP_PASSWORD_RE.fullmatch(password):
        await context.bot.send_message(message.chat_id, "App password harus 16 huruf. Coba kirim lagi (atau ketik batal).")
        return
    address = form["address"]
    msg = await context.bot.send_message(message.chat_id, f"⏳ Mencoba login ke {address}...")
    try:
        await asyncio.to_thread(gmail_accounts.verify, address, password)
    except Exception as e:
        log.info("verifikasi akun %s gagal: %s", address, e)
        await msg.edit_text(f"❌ Login ke {address} gagal: {e}\n\nPastikan app password benar dan IMAP aktif "
                            "(Gmail > Setelan > Penerusan dan POP/IMAP). Kirim app password lagi, atau ketik batal.")
        return
    gmail_accounts.save(address, password)
    context.user_data["gmail_form"] = None
    text, markup = accounts_view(load_user(uid))
    await msg.edit_text(f"✅ {address} {'diperbarui' if form['edit'] else 'ditambahkan'}.\n\n" + text,
                        reply_markup=markup)
