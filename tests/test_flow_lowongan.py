"""Alur lowongan: analisis, peringatan, penyesuaian CV (PAR, pratinjau skor, tingkatkan lagi), foto, coba lagi."""
import json

import pytest

import ai_helper
from conftest import JOB_TEXT, PROFILE


def analysis(**extra):
    base = {"posisi": "Backend Developer", "perusahaan": "PT Maju Jaya", "email_hr": "hr@majujaya.co.id",
            "bahasa_lowongan": "id", "skor": 80, "skill_cocok": ["Laravel"], "skill_kurang": ["Docker"],
            "alasan": "Cukup cocok.", "lokasi": "Malang", "mode_kerja": "wfo", "tanda_penipuan": []}
    base.update(extra)
    return base


@pytest.fixture
def ai(monkeypatch):
    """AI palsu yang bisa diatur per tes."""
    state = {"analysis": analysis(), "tailor_calls": []}

    def analyze_job(profile, job_text):
        if callable(state["analysis"]):
            return state["analysis"](profile, job_text)
        return json.loads(json.dumps(state["analysis"]))

    def tailor_cv(profile, job_text, lang, facts=None, ask_missing=True, skills=None):
        state["tailor_calls"].append({"facts": facts or [], "ask": ask_missing, "skills": [k["skill"] for k in skills or []]})
        out = json.loads(json.dumps(profile))
        out["skill"] = profile["skill"] + [k["skill"] for k in skills or []] + ["Kubernetes"]  # Kubernetes = karangan
        out["pengalaman"][0]["poin"] = ["PAR: " + p for p in profile["pengalaman"][0]["poin"]]
        return {"profile": out, "perubahan": ["Poin ditulis dengan PAR"], "saran_belajar": ["Redis"],
                "perlu_hasil": state.get("perlu_hasil", []) if ask_missing else []}

    monkeypatch.setattr(ai_helper, "analyze_job", analyze_job)
    monkeypatch.setattr(ai_helper, "tailor_cv", tailor_cv)
    return state


def scored_by_skills(profile, job_text):
    have = {s.lower() for s in profile.get("skill") or []}
    missing = [s for s in ("Docker", "Redis") if s.lower() not in have]
    return analysis(skor=100 - 20 * len(missing), skill_cocok=sorted(have), skill_kurang=missing)  # awal 60 < ambang 70


def test_tanpa_cv_diminta_kirim_cv_dulu(user, ai):
    assert "Kirim CV kamu dulu" in user.texts(user.say(JOB_TEXT))


def test_teks_terlalu_pendek(with_cv):
    assert "terlalu pendek" in with_cv.texts(with_cv.say("lowongan dev"))


def test_skor_tinggi_tampil_ringkasan_dan_pilihan(with_cv, ai):
    log = with_cv.say(JOB_TEXT)
    text = with_cv.texts(log)
    assert "📋 Backend Developer - PT Maju Jaya" in text and "Skor kecocokan: 80/100" in text
    assert with_cv.buttons(log) == ["✍️ Buat Email Lamaran|cl", "🛠️ Sesuaikan CV|tailor", "❌ Lewati|skip"]


def test_peringatan_penipuan_dobel_dan_preferensi(with_cv, ai):
    with_cv.set_data(pref={"lokasi": ["Surabaya"]}, lamaran=[{
        "id": 1, "posisi": "Backend Developer", "perusahaan": "PT. Maju Jaya", "status": "terkirim",
        "tanggal": "2026-11-01T09:00+07:00"}], next_id=2)
    text = with_cv.texts(with_cv.say(JOB_TEXT + " Ada biaya pendaftaran Rp100.000, transfer ke rekening admin."))
    assert "🚨 Waspada kemungkinan penipuan" in text
    assert "🔁 Kamu sudah pernah melamar ke PT Maju Jaya" in text
    assert "🎯 Tidak sesuai preferensi kamu" in text and "Lokasi Malang" in text


def test_analisis_gagal_bisa_coba_lagi(with_cv, ai):
    def fail(profile, text):
        raise RuntimeError("Server Gemini sedang sibuk")
    ai["analysis"] = fail
    log = with_cv.say(JOB_TEXT)
    assert "Gagal menganalisis lowongan: Server Gemini sedang sibuk" in with_cv.texts(log)
    assert with_cv.buttons(log) == ["🔄 Coba lagi|retryjob"]
    ai["analysis"] = analysis()
    assert "Skor kecocokan: 80/100" in with_cv.texts(with_cv.press("retryjob"))


def test_foto_lowongan(with_cv, ai, monkeypatch):
    monkeypatch.setattr(ai_helper, "read_job_image", lambda image, mime, caption: JOB_TEXT)
    log = with_cv.send_file("poster.jpg", b"\xff\xd8 jpeg")
    assert "Skor kecocokan: 80/100" in with_cv.texts(log)


class TestPenyesuaianCv:
    def test_skor_rendah_disesuaikan_tapi_pdf_belum_dibuat(self, with_cv, ai):
        ai["analysis"] = scored_by_skills
        log = with_cv.say(JOB_TEXT)
        text = with_cv.texts(log)
        assert "Skor: 60 → 60" in text and "PDF belum dibuat" in text
        assert not [e for e in log if e["kind"] == "document"]
        assert with_cv.buttons(log)[-4:] == ["✅ Pakai skor ini, buat CV|tl:ok", "🚀 Tingkatkan lagi|tl:more",
                                             "↩️ Pakai CV asli|orig", "❌ Lewati|skip"]

    def test_pakai_skor_ini_membuat_pdf_tanpa_skill_karangan(self, with_cv, ai):
        with_cv.say(JOB_TEXT)
        log = with_cv.press("tailor")
        assert "PDF belum dibuat" in with_cv.texts(log)
        log = with_cv.press("tl:ok")
        doc = next(e for e in log if e["kind"] == "document")
        assert doc["content"].startswith(b"%PDF") and doc["filename"] == "CV_Fahmi_Dafrin_Backend_Developer.pdf"
        import cv_parser
        pdf_text = cv_parser.extract_text(with_cv.context.user_data["tailored_cv"])
        assert "PAR: Membuat sistem kasir" in pdf_text and "Kubernetes" not in pdf_text

    def test_tingkatkan_lagi_dengan_skill_yang_dikonfirmasi(self, with_cv, ai):
        ai["analysis"] = scored_by_skills
        with_cv.say(JOB_TEXT)
        log = with_cv.press("tl:more")
        assert "Lowongan ini meminta: Docker" in with_cv.texts(log)
        with_cv.press("gap:yes")
        with_cv.say("Pakai Docker untuk deploy aplikasi kasir")
        log = with_cv.press("gap:no")
        text = with_cv.texts(log)
        assert ai["tailor_calls"][-1]["skills"] == ["Docker"]
        assert "Tambahkan Docker ke 📄 CV Saya juga?" in text
        assert "Skor: 60 → 80" in text and "skor maksimal yang jujur" in text
        data = with_cv.data()
        assert data["skill_konfirmasi"][0]["skill"] == "Docker" and data["skill_belum"] == ["Redis"]
        assert data["profile"]["skill"] == PROFILE["skill"]  # belum ditambahkan tanpa persetujuan
        with_cv.press("gap:addcv")
        assert with_cv.data()["profile"]["skill"] == PROFILE["skill"] + ["Docker"]

    def test_pertanyaan_par_dijawab_dan_dipakai(self, with_cv, ai):
        ai["perlu_hasil"] = [{"poin_asli": "membuat sistem kasir.", "pertanyaan": "Apa dampaknya?"}]
        with_cv.say(JOB_TEXT)
        log = with_cv.press("tailor")
        assert 'Poin: "Membuat sistem kasir"' in with_cv.texts(log)  # dicocokkan ke poin asli
        log = with_cv.say("Antrean turun dari 10 menit jadi 3 menit")
        assert ai["tailor_calls"][-1]["facts"] == [{"poin": "Membuat sistem kasir", "hasil": "Antrean turun dari 10 menit jadi 3 menit"}]
        assert "PDF belum dibuat" in with_cv.texts(log)

    def test_pakai_cv_asli_mengembalikan_skor(self, with_cv, ai):
        ai["analysis"] = scored_by_skills
        with_cv.say(JOB_TEXT)
        with_cv.press("orig")
        assert with_cv.context.user_data["job"]["skor"] == 60 and with_cv.context.user_data["tailored_cv"] is None

    def test_tombol_lama_tanpa_lowongan_aktif(self, with_cv):
        assert "Sesi lowongan sudah habis" in with_cv.texts(with_cv.press("tl:ok"))
