"""Perlengkapan tes bersama.

Tes berjalan di folder sementara (bot menyimpan data di ./data), tanpa internet: AI, Gmail, dan font diganti
versi palsu. Tes alur bot memakai pintu masuk yang sama dengan Telegram (tombol, teks, dokumen, jadwal), jadi
tetap berlaku walau isi bot dipecah ke modul lain.
"""
import asyncio
import datetime as dt
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parent.parent
WORKDIR = Path(tempfile.mkdtemp(prefix="cvbot-test-"))
os.chdir(WORKDIR)
sys.path.insert(0, str(REPO))
for key in [k for k in os.environ if k.startswith(("GMAIL_", "GEMINI_", "OPENAI_", "OLLAMA_", "CLAUDE_", "ANTHROPIC_",
                                                   "AI_PROVIDER", "ALERT_"))]:
    del os.environ[key]
os.environ.update({"TELEGRAM_BOT_TOKEN": "1:test", "ALLOWED_USER_IDS": "1", "MAX_SEND_PER_DAY": "3",
                   "TIMEZONE": "Asia/Jakarta"})

TZ = ZoneInfo("Asia/Jakarta")
UID = 1


def weekday_at(weekday: int, hour: int, minute: int = 0) -> dt.datetime:
    """Waktu di minggu yang sama dengan 9 Nov 2026 (0 = Senin)."""
    monday = dt.datetime(2026, 11, 9, tzinfo=TZ)
    monday -= dt.timedelta(days=monday.weekday())
    return monday + dt.timedelta(days=weekday, hours=hour, minutes=minute)


# ---------------------------------------------------------------- Telegram palsu
def _markup(m):
    if m is None:
        return None
    rows = getattr(m, "inline_keyboard", None)
    if rows is None:
        return ["(keyboard menu)"]
    return [f"{b.text}|{b.callback_data or b.url}" for row in rows for b in row]


class FakeMessage:
    """Pesan Telegram palsu. reply_markup wajib argumen bernama, persis seperti python-telegram-bot."""

    def __init__(self, log: list, text: str = "", document=None, caption=None, photo=None):
        self.log, self.text, self.document, self.caption, self.photo = log, text, document, caption, photo
        self.chat_id = UID

    async def reply_text(self, text, *, reply_markup=None, **kw):
        self.log.append({"kind": "reply", "text": text, "buttons": _markup(reply_markup)})
        return FakeMessage(self.log)

    async def edit_text(self, text, *, reply_markup=None, **kw):
        self.log.append({"kind": "edit", "text": text, "buttons": _markup(reply_markup)})
        return self

    async def edit_reply_markup(self, reply_markup=None):
        pass

    async def delete(self):
        self.log.append({"kind": "delete", "text": self.text})

    async def reply_document(self, document, *, filename=None, caption=None, reply_markup=None):
        self.log.append({"kind": "document", "text": caption or "", "filename": filename,
                         "content": document.read(), "buttons": _markup(reply_markup)})


class FakeBot:
    def __init__(self, log):
        self.log = log

    async def send_message(self, chat_id, text, *, reply_markup=None, disable_notification=None, **kw):
        self.log.append({"kind": "send", "text": text, "buttons": _markup(reply_markup), "silent": disable_notification})
        return FakeMessage(self.log)


class FakeJob:
    def __init__(self, callback, when, data, name):
        self.callback, self.when, self.data, self.name, self.removed = callback, when, data, name, False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, callback, when, data=None, name=None, chat_id=None, user_id=None):
        self.jobs.append(FakeJob(callback, when, data, name))

    def get_jobs_by_name(self, name):
        return [j for j in self.jobs if j.name == name]


class Harness:
    """Satu pengguna Telegram yang memakai bot: tekan tombol, ketik teks, kirim file."""

    def __init__(self, bot_module):
        self.bot = bot_module
        self.log = []
        self.context = SimpleNamespace(user_data={}, bot=FakeBot(self.log), args=[], job_queue=FakeJobQueue())
        self.user = SimpleNamespace(id=UID)

    def _update(self, message):
        return SimpleNamespace(message=message, effective_user=self.user, effective_message=message,
                               effective_chat=SimpleNamespace(id=UID), callback_query=None)

    def press(self, data: str) -> list:
        self.log.clear()

        async def noop(*a):
            pass

        message = FakeMessage(self.log)
        query = SimpleNamespace(data=data, message=message, answer=noop, edit_message_reply_markup=noop)
        update = SimpleNamespace(callback_query=query, effective_user=self.user, effective_message=message,
                                 effective_chat=SimpleNamespace(id=UID))
        asyncio.run(self.bot.handle_button(update, self.context))
        return list(self.log)

    def say(self, text: str) -> list:
        self.log.clear()
        asyncio.run(self.bot.handle_text(self._update(FakeMessage(self.log, text=text)), self.context))
        return list(self.log)

    def command(self, name: str) -> list:
        """Jalankan /perintah lewat handler yang benar-benar didaftarkan di aplikasi Telegram."""
        self.log.clear()
        handler = next(h for group in built_app(self.bot).handlers.values() for h in group
                       if name in getattr(h, "commands", ()))
        asyncio.run(handler.callback(self._update(FakeMessage(self.log, text=f"/{name}")), self.context))
        return list(self.log)

    def scheduled_job(self, name: str):
        """Tugas terjadwal (cek harian, cek balasan) seperti yang didaftarkan saat bot start."""
        job = next(j for j in built_app(self.bot).job_queue.jobs() if j.name == name)
        return FakeJob(job.callback, None, job.data, name)

    def send_file(self, name: str, content: bytes) -> list:
        self.log.clear()

        async def get_file():
            async def download_to_drive(path):
                Path(path).write_bytes(content)

            async def download_as_bytearray():
                return bytearray(content)
            return SimpleNamespace(download_to_drive=download_to_drive, download_as_bytearray=download_as_bytearray)

        document = SimpleNamespace(file_name=name, file_size=len(content), get_file=get_file)
        asyncio.run(self.bot.handle_document(self._update(FakeMessage(self.log, document=document)), self.context))
        return list(self.log)

    def run_scheduled(self, job) -> list:
        self.log.clear()
        context = SimpleNamespace(bot=FakeBot(self.log), job=SimpleNamespace(data=job.data, user_id=UID, chat_id=UID),
                                  job_queue=self.context.job_queue, user_data=self.context.user_data,
                                  application=SimpleNamespace(user_data={UID: self.context.user_data}))
        asyncio.run(job.callback(context))
        return list(self.log)

    @staticmethod
    def texts(log: list) -> str:
        return "\n||\n".join(entry["text"] for entry in log)

    @staticmethod
    def buttons(log: list) -> list:
        return [b for entry in log for b in entry.get("buttons") or []]

    def data(self) -> dict:
        import storage
        import tracker
        data = storage.read_json(Path("data") / f"{UID}.json", {})
        tracker.migrate(data, "Asia/Jakarta")
        return data

    def set_data(self, **values) -> None:
        import storage
        data = self.data()
        data.update(values)
        storage.write_json(Path("data") / f"{UID}.json", data)


_APP = {}


def built_app(bot_module):
    """Aplikasi Telegram yang dirakit bot.main() (tanpa benar-benar terhubung ke Telegram)."""
    if "app" not in _APP:
        from telegram.ext import Application
        original = Application.run_polling
        Application.run_polling = lambda self, *a, **k: _APP.update(app=self)
        try:
            bot_module.main()
        finally:
            Application.run_polling = original
    return _APP["app"]


# ---------------------------------------------------------------- fixture
@pytest.fixture(autouse=True)
def clean_data():
    """Setiap tes mulai dengan folder data kosong (kecuali log bot yang sedang terbuka)."""
    data_dir = WORKDIR / "data"
    if data_dir.exists():
        for item in data_dir.iterdir():
            if item.name == "bot.log":
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    yield


@pytest.fixture
def bot(monkeypatch):
    import ai_helper
    import bot as bot_module
    import cv_fonts
    import tracker

    # Tanpa internet: font bawaan PDF, AI & Gmail harus diganti di tiap tes yang memakainya
    monkeypatch.setattr(cv_fonts, "ensure", lambda family: ("Helvetica", "Helvetica-Bold", True))

    def no_ai(*a, **k):
        raise AssertionError("AI dipanggil tanpa diganti versi palsu di tes ini")
    monkeypatch.setattr(ai_helper, "_ask", no_ai)
    monkeypatch.setattr(tracker, "now", lambda tz: weekday_at(1, 10))  # Selasa 10.00, jam kerja
    return bot_module


@pytest.fixture
def user(bot):
    return Harness(bot)


@pytest.fixture
def gmail(monkeypatch):
    """Satu akun Gmail palsu; email yang 'terkirim' dicatat di sent."""
    import gmail_accounts
    import mailer

    account = gmail_accounts.Account("saya@gmail.com", "apppassword")
    monkeypatch.setattr(gmail_accounts, "_env_accounts", lambda: [account])
    sent = []

    def send(acc, to, subject, body, attachment=None, in_reply_to=None):
        sent.append({"from": acc.address, "to": to, "subject": subject, "body": body, "attachment": attachment,
                     "in_reply_to": in_reply_to})
        return f"<msg{len(sent)}@test>"
    monkeypatch.setattr(mailer, "send_application", send)
    return sent


PROFILE = {
    "nama": "Fahmi Dafrin", "email": "fahmi@mail.com", "telepon": "0812", "lokasi": "Malang",
    "tautan": ["dafrin.id"], "ringkasan": "Programmer web.", "skill": ["Laravel", "MySQL"],
    "pengalaman": [{"posisi": "Programmer", "perusahaan": "PT Lama", "periode": "2022 - Sekarang",
                    "poin": ["Membuat sistem kasir", "Membuat API"]}],
    "proyek": [{"nama": "Aplikasi Kasir", "poin": ["Kasir untuk UMKM"]}],
    "pendidikan": ["S1 Informatika"],
}

JOB_TEXT = ("Dicari Backend Developer di PT Maju Jaya, Malang. Kualifikasi: Laravel, MySQL, Docker, Redis. "
            "Kirim CV ke hr@majujaya.co.id paling lambat akhir bulan. Kerja WFO di kantor Malang.")


@pytest.fixture
def with_cv(user):
    """Pengguna yang sudah mengunggah CV."""
    cv = Path("data") / str(UID) / "original" / "cv.pdf"
    cv.parent.mkdir(parents=True, exist_ok=True)
    cv.write_bytes(b"%PDF-1.4 cv asli")
    user.set_data(profile=PROFILE, cv_path=str(cv), font_asli="Calibri")
    return user
