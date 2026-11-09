"""Email lamaran, konfirmasi, kirim, jadwal jam kerja, batas harian, antrean."""
import pytest

import ai_helper
import tracker
from conftest import JOB_TEXT, weekday_at


@pytest.fixture
def ready(with_cv, gmail, monkeypatch):
    """Lowongan sudah dianalisis dan email lamaran sudah dibuat."""
    monkeypatch.setattr(ai_helper, "analyze_job", lambda p, t: {
        "posisi": "Backend Developer", "perusahaan": "PT Maju Jaya", "email_hr": "hr@majujaya.co.id",
        "bahasa_lowongan": "id", "skor": 80})
    monkeypatch.setattr(ai_helper, "write_cover_letter", lambda profile, job, text, gaya: f"Yth. HRD ({gaya})")
    with_cv.say(JOB_TEXT)
    log = with_cv.press("cl")
    assert "Yth. HRD (formal)" in with_cv.texts(log)
    assert with_cv.buttons(log)[:2] == ["📧 Kirim via Gmail|send", "🔄 Buat Ulang|cl"]
    return with_cv


def set_now(monkeypatch, when):
    monkeypatch.setattr(tracker, "now", lambda tz: when)


def test_konfirmasi_menampilkan_tujuan_dan_lampiran(ready):
    text = ready.texts(ready.press("send"))
    assert "Ke: hr@majujaya.co.id" in text and "Lampiran: cv.pdf (CV asli)" in text
    assert "Subjek: Lamaran Backend Developer - Fahmi Dafrin" in text


def test_kirim_di_jam_kerja_langsung_terkirim_dan_tercatat(ready, gmail):
    ready.press("send")
    log = ready.press("confirm:0")
    assert gmail[-1]["to"] == "hr@majujaya.co.id" and gmail[-1]["body"] == "Yth. HRD (formal)"
    assert gmail[-1]["attachment"].endswith("cv.pdf")
    assert "✅ Lamaran #1 terkirim ke hr@majujaya.co.id dari saya@gmail.com" in ready.texts(log)
    app = ready.data()["lamaran"][0]
    assert app["dari"] == "saya@gmail.com" and app["message_id"] == "<msg1@test>"


def test_ganti_email_tujuan(ready):
    ready.press("send")
    ready.press("editto")
    assert "Format email tidak valid" in ready.texts(ready.say("bukan email"))
    assert "Ke: lain@maju.co.id" in ready.texts(ready.say("lain@maju.co.id"))


def test_di_luar_jam_kerja_dijadwalkan_lalu_terkirim(ready, gmail, monkeypatch):
    set_now(monkeypatch, weekday_at(4, 23))  # Jumat 23.00
    assert "🕗 Sekarang di luar jam kerja" in ready.texts(ready.press("send"))
    log = ready.press("confirm:0")
    assert not gmail and "dijadwalkan terkirim Senin" in ready.texts(log)
    job = ready.context.job_queue.jobs[-1]
    assert job.when == weekday_at(7, 8)
    set_now(monkeypatch, weekday_at(7, 8))
    log = ready.run_scheduled(job)
    assert gmail[-1]["to"] == "hr@majujaya.co.id" and ready.data()["antrian"] == []
    assert "Lamaran #1 terkirim" in ready.texts(log)


def test_jadwal_jam_kerja_bisa_dimatikan(ready, gmail, monkeypatch):
    set_now(monkeypatch, weekday_at(4, 23))
    ready.press("set:tog:jadwal_kirim")
    ready.press("send")
    ready.press("confirm:0")
    assert gmail


def test_batas_harian_lalu_jadwalkan_atau_tetap_kirim(ready, gmail):
    ready.set_data(lamaran=[{"id": i, "dari": "saya@gmail.com", "tanggal": weekday_at(1, 9).isoformat(),
                             "status": "terkirim", "perusahaan": f"PT {i}"} for i in range(3)], next_id=3)
    ready.press("send")
    log = ready.press("confirm:0")
    assert not gmail and "sudah mengirim 3 lamaran hari ini (batas 3)" in ready.texts(log)
    assert "📤 Tetap kirim sekarang|confirm:0:now" in ready.buttons(log)
    ready.press("confirm:0:now")
    assert len(gmail) == 1


def test_antrean_batal_dan_gagal_kirim(ready, gmail, monkeypatch):
    import mailer
    ready.press("send")
    ready.press("confirm:0:later")
    qid = ready.data()["antrian"][0]["id"]
    assert f"🕗 Antrean kirim (1)|q:list" in ready.buttons(ready.press("lamlist"))

    def down(*a, **k):
        raise RuntimeError("SMTP down")
    monkeypatch.setattr(mailer, "send_application", down)
    log = ready.press(f"q:now:{qid}")
    assert "gagal dikirim: SMTP down" in ready.texts(log) and len(ready.data()["antrian"]) == 1
    ready.press(f"q:del:{qid}")
    assert ready.data()["antrian"] == [] and ready.context.job_queue.jobs[-1].removed
    assert "sudah tidak ada di antrean" in ready.texts(ready.press(f"q:now:{qid}"))


def test_cv_asli_yang_diubah_ikut_dilampirkan(ready, gmail):
    ready.press("cv:f:telepon")
    ready.say("0899")
    ready.press("send")
    ready.press("confirm:0")
    assert gmail[-1]["attachment"].endswith("CV_Fahmi_Dafrin.pdf")


def test_revisi_email(ready, monkeypatch):
    monkeypatch.setattr(ai_helper, "revise_text", lambda text, instruction, kind: f"{text} [{instruction}]")
    ready.context.args = ["lebih", "singkat"]
    assert "Yth. HRD (formal) [lebih singkat]" in ready.texts(ready.command("revisi"))
