"""📂 Lamaran: balasan HR, persiapan interview, follow-up, rekomendasi harian, statistik, backup, startup."""
import io
import json
import zipfile
from pathlib import Path

import pytest

import ai_helper
import gmail_replies
import job_alerts
from conftest import UID, weekday_at

APPS = [
    {"id": 1, "posisi": "Backend Developer", "perusahaan": "PT Maju", "ke": "hr@maju.co.id", "dari": "saya@gmail.com",
     "status": "terkirim", "tanggal": weekday_at(0, 9).isoformat(), "subjek": "Lamaran Backend Developer",
     "message_id": "<m1>", "job_text": "Backend Laravel", "followups": [], "balasan": [], "riwayat_status": []},
    {"id": 2, "posisi": "QA", "perusahaan": "PT Lama", "ke": "hr@lama.co.id", "dari": "saya@gmail.com",
     "status": "terkirim", "tanggal": "2026-10-01T09:00+07:00", "followups": [], "balasan": [], "riwayat_status": []},
]


@pytest.fixture
def applied(with_cv, gmail):
    with_cv.set_data(lamaran=json.loads(json.dumps(APPS)), next_id=3)
    return with_cv


def test_daftar_dan_ubah_status(applied):
    assert "#2 PT Lama|app:2" in applied.buttons(applied.press("lamlist"))
    applied.press("st:1:interview")
    assert applied.data()["lamaran"][0]["status"] == "interview"


def test_cek_balasan_hr_dan_tombol_interview(applied, monkeypatch):
    reply = {"app_id": 1, "id": "<r1>", "from": "HR Maju", "subject": "Undangan interview", "text": "Senin jam 10"}
    # seperti aslinya: balasan yang sudah dibaca (seen) tidak dikembalikan lagi
    monkeypatch.setattr(gmail_replies, "find_replies",
                        lambda acc, apps, seen, sent, per_query=10: [] if reply["id"] in seen else [reply])
    monkeypatch.setattr(ai_helper, "classify_reply", lambda app, sender, subject, text: {
        "jenis": "interview", "ringkasan": "Diundang interview", "jadwal": "Senin 10.00"})
    log = applied.say("📬 Cek Balasan")
    assert "🎤 Siapkan interview|iv:1" in applied.buttons(log) and "Senin 10.00" in applied.texts(log)
    assert applied.data()["lamaran"][0]["status"] == "interview"
    assert "Belum ada balasan baru" in applied.texts(applied.say("📬 Cek Balasan"))  # sudah dibaca


def test_persiapan_dan_latihan_interview(applied, monkeypatch):
    calls = []
    monkeypatch.setattr(ai_helper, "interview_prep", lambda profile, app, skills, facts: calls.append(1) or {
        "pertanyaan": [{"q": "Ceritakan proyek Laravel", "tipe": "pengalaman", "tips": "Pakai kasir"},
                       {"q": "Apa itu REST?", "tipe": "teknis"}],
        "tanya_balik": ["Bagaimana timnya?"], "tips": ["Riset perusahaan"]})
    monkeypatch.setattr(ai_helper, "interview_feedback", lambda q, a, p, pos: f"Bagus, tambah contoh ({a})")
    log = applied.press("iv:1")
    assert "1. Ceritakan proyek Laravel (pengalaman)" in applied.texts(log)
    applied.press("iv:1")
    assert calls == [1]  # disimpan, tidak memanggil AI lagi
    assert "Latihan 1/2" in applied.texts(applied.press("iv:p:1"))
    log = applied.say("Saya membuat kasir dengan Laravel")
    assert "Bagus, tambah contoh (Saya membuat kasir dengan Laravel)" in applied.texts(log) and "Latihan 2/2" in applied.texts(log)
    assert "Latihan selesai" in applied.texts(applied.press("iv:x"))


def test_follow_up_di_thread_yang_sama(applied, gmail, monkeypatch):
    monkeypatch.setattr(ai_helper, "write_followup", lambda profile, app, days: "Halo, menindaklanjuti lamaran saya.")
    applied.press("fu:1")
    applied.press("fusend")
    assert gmail[-1]["in_reply_to"] == "<m1>" and gmail[-1]["subject"] == "Re: Lamaran Backend Developer"
    assert len(applied.data()["lamaran"][0]["followups"]) == 1


def test_cek_harian_rekomendasi_tersaring_dan_pengingat_follow_up(applied, monkeypatch):
    monkeypatch.setattr(job_alerts, "fetch_alert_emails", lambda seen, days, acc: [
        {"id": "<e1>", "text": "alert", "from": "JobStreet", "subject": "Lowongan", "links": {"L1": "https://js.com/1"}}])
    monkeypatch.setattr(ai_helper, "extract_and_rank", lambda profile, text, sender: [
        {"posisi": "Sales Manager", "perusahaan": "PT A", "skor": 90},
        {"posisi": "Backend Developer", "perusahaan": "PT. Maju", "skor": 88},
        {"posisi": "Data Engineer", "perusahaan": "PT C", "skor": 80, "link_ref": "L1"}])
    applied.set_data(pref={"hindari": ["sales"]})
    log = applied.run_scheduled(applied.scheduled_job(f"daily_{UID}"))
    text = applied.texts(log)
    assert "1 lowongan yang cocok" in text and "Data Engineer" in text and "Sales" not in text.split("disaring")[0]
    assert "2 lowongan lain disaring" in text
    assert "⏰ 1 lamaran belum ada kabar" in text and "#2 QA - PT Lama" in text
    assert all(e["silent"] for e in log if e["kind"] == "send")  # rutin: tanpa suara


def test_statistik(applied):
    assert "Total terkirim: 2" in applied.texts(applied.say("📊 Statistik"))


class TestBackup:
    def test_backup_tanpa_rahasia_lalu_pulihkan(self, applied):
        import app_settings
        app_settings.set("GEMINI_API_KEY", "rahasia")
        doc = next(e for e in applied.command("backup") if e["kind"] == "document")
        archive = zipfile.ZipFile(io.BytesIO(doc["content"]))
        assert "1.json" in archive.namelist() and "1/original/cv.pdf" in archive.namelist()
        assert "rahasia" not in str(doc["content"])

        old = json.loads(archive.read("1.json"))
        old["profile"]["nama"] = "Dari Backup"
        old["cv_path"] = "data/999/original/cv.pdf"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("999.json", json.dumps(old))
            z.writestr("999/original/cv.pdf", b"%PDF-backup")
        assert "♻️ Pulihkan|bk:restore" in applied.buttons(applied.send_file("backup.zip", buffer.getvalue()))
        applied.press("bk:restore")
        data = applied.data()
        assert data["profile"]["nama"] == "Dari Backup"
        assert Path(data["cv_path"]).read_bytes() == b"%PDF-backup" and Path(data["cv_path"]).parts[-3] == str(UID)
        assert list((Path("data") / str(UID)).glob("sebelum-pulih-*.zip"))

    def test_zip_berbahaya_ditolak(self, user):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("1.json", "{}")
            z.writestr("../../jahat.txt", "x")
        assert "tidak bisa dipulihkan" in user.texts(user.send_file("backup.zip", buffer.getvalue()))
        assert "tidak bisa dipulihkan" in user.texts(user.send_file("backup.zip", b"bukan zip"))


def test_bot_start_mendaftarkan_semua(user):
    from conftest import built_app
    app = built_app(user.bot)
    commands = {c for group in app.handlers.values() for h in group for c in getattr(h, "commands", ())}
    assert {"start", "cv", "lamaran", "rekomendasi", "cekbalasan", "pengaturan", "backup", "revisi"} <= commands
    assert {j.name for j in app.job_queue.jobs()} >= {f"daily_{UID}", f"replies_{UID}"}
