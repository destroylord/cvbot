"""Bot start tahan gangguan jaringan (mis. 'telegram.error.TimedOut' di VPS)."""
import asyncio
from types import SimpleNamespace

from telegram.error import TimedOut


def test_gagal_pasang_daftar_perintah_tidak_mematikan_bot(bot, caplog):
    async def set_my_commands(commands):
        raise TimedOut("Timed out")
    app = SimpleNamespace(bot=SimpleNamespace(set_my_commands=set_my_commands))
    asyncio.run(bot.post_init(app))  # tidak boleh melempar error
    assert "daftar perintah Menu belum terpasang" in caplog.text


def test_start_terus_mencoba_dan_batas_tunggu_lebih_panjang(bot, monkeypatch):
    from telegram.ext import Application
    captured = {}
    monkeypatch.setattr(Application, "run_polling", lambda self, **kw: captured.update(app=self, kw=kw))
    bot.main()
    assert captured["kw"].get("bootstrap_retries") == -1
    timeout = captured["app"].bot.request._client_kwargs["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (20, 30, 30, 10)
