"""Menu: 📄 CV Saya, ⚙️ Pengaturan, 🤖 Model AI, 📧 Akun Gmail, 🎯 Preferensi, 🔤 Font."""
import pytest

import ai_helper
import app_settings
import gmail_accounts
from conftest import PROFILE, UID


class TestCvSaya:
    def test_ringkasan_cv_dan_tombol(self, with_cv):
        log = with_cv.say("📄 CV Saya")
        assert "Programmer web." in with_cv.texts(log)
        assert "💼 Pengalaman (1)|cv:e:pengalaman" in with_cv.buttons(log)
        assert "✨ Perbaiki poin dengan PAR|cv:par" in with_cv.buttons(log)

    def test_ubah_ringkasan_dan_lokasi(self, with_cv):
        with_cv.press("cv:f:ringkasan")
        with_cv.say("Programmer web 4 tahun.")
        with_cv.press("cv:f:lokasi")
        with_cv.say("-")
        profile = with_cv.data()["profile"]
        assert profile["ringkasan"] == "Programmer web 4 tahun." and profile["lokasi"] is None
        assert with_cv.data()["profile_edited"] is True

    def test_tambah_skill_dipisah_koma(self, with_cv):
        with_cv.press("cv:la:skill")
        with_cv.say("Laravel, REST API, CI/CD")
        assert with_cv.data()["profile"]["skill"] == ["Laravel", "MySQL", "REST API", "CI/CD"]

    def test_tambah_pengalaman_bertahap(self, with_cv):
        with_cv.press("cv:ea:pengalaman")
        assert "wajib diisi" in with_cv.texts(with_cv.say("-"))
        for text in ("Backend Developer", "PT Baru", "Jan 2025 - Sekarang"):
            with_cv.say(text)
        with_cv.say("Membuat API pemesanan\nIntegrasi Midtrans")
        first = with_cv.data()["profile"]["pengalaman"][0]
        assert first["perusahaan"] == "PT Baru" and first["poin"] == ["Membuat API pemesanan", "Integrasi Midtrans"]

    def test_hapus_pengalaman_dengan_konfirmasi(self, with_cv):
        log = with_cv.press("cv:ed:pengalaman:0")
        assert "🗑️ Ya, hapus|cv:edok:pengalaman:0" in with_cv.buttons(log)
        with_cv.press("cv:edok:pengalaman:0")
        assert with_cv.data()["profile"]["pengalaman"] == []

    def test_tombol_lama_tidak_crash(self, with_cv):
        assert "Data CV sudah berubah" in with_cv.texts(with_cv.press("cv:ei:pengalaman:9"))

    def test_unduh_pdf(self, with_cv):
        with_cv.press("cv:f:telepon")
        with_cv.say("0899")
        doc = next(e for e in with_cv.press("cv:dl") if e["kind"] == "document")
        assert doc["content"].startswith(b"%PDF") and "versi terbaru" in doc["text"]

    def test_perbaiki_par_pratinjau_lalu_simpan(self, with_cv, monkeypatch):
        monkeypatch.setattr(ai_helper, "improve_cv_par", lambda profile, facts=None, ask_missing=True: {
            "pengalaman": [["Antre lama; membuat sistem kasir", "Butuh integrasi; membuat API, error turun 50%"]],
            "proyek": [["UMKM butuh kasir; membuat aplikasi kasir"]], "perubahan": ["PAR"], "perlu_hasil": []})
        log = with_cv.press("cv:par")
        text = with_cv.texts(log)
        assert "✨ Pratinjau perbaikan PAR" in text and "1 poin dikembalikan ke aslinya" in text  # 50% karangan
        assert with_cv.data()["profile"]["pengalaman"][0]["poin"] == PROFILE["pengalaman"][0]["poin"]
        with_cv.press("cv:parok")
        assert with_cv.data()["profile"]["pengalaman"][0]["poin"] == ["Antre lama; membuat sistem kasir", "Membuat API"]


class TestPengaturan:
    def test_tampilan_dan_toggle(self, user):
        log = user.say("⚙️ Pengaturan")
        assert "🕗 Kirim di jam kerja: ON ✅|set:tog:jadwal_kirim" in user.buttons(log)
        user.press("set:tog:auto_rekomendasi")
        assert user.data()["auto_rekomendasi"] is False

    def test_gaya_email(self, user):
        user.press("gaya:singkat")
        assert user.data()["gaya"] == "singkat"

    def test_preferensi(self, user):
        user.press("pref:gaji")
        assert "Tidak terbaca" in user.texts(user.say("abc"))
        user.say("6,5 jt")
        user.press("pref:lok")
        user.say("Malang, Surabaya")
        user.press("pref:mode:remote")
        assert user.data()["pref"] == {"gaji_min": 6_500_000, "lokasi": ["Malang", "Surabaya"], "mode": "remote"}

    def test_font(self, with_cv):
        log = with_cv.press("font")
        assert "Font CV asli kamu: Calibri" in with_cv.texts(log)
        with_cv.press("font:type")
        with_cv.say("Raleway")
        assert with_cv.data()["font_cv"] == "Raleway"

    def test_tombol_menu_membatalkan_isian(self, user):
        user.press("pref:gaji")
        user.say("📂 Lamaran")
        assert user.say("5jt") and user.data().get("pref", {}).get("gaji_min") is None


class TestModelAi:
    @pytest.fixture(autouse=True)
    def models(self, monkeypatch):
        def list_models(name, key=None):
            if key == "salah":
                raise RuntimeError("API key tidak valid.")
            return ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
        monkeypatch.setattr(ai_helper, "list_models", list_models)

    def test_pilih_model_dari_daftar(self, user):
        log = user.press("ai:model")
        assert "✅ gemini-3.6-flash|ai:set:m:0" in user.buttons(log)  # model aktif (default) ditandai
        user.press("ai:set:m:1")
        assert app_settings.get("GEMINI_MODEL") == "gemini-3.5-flash-lite"

    def test_api_key_dihapus_dari_chat_dan_diperiksa(self, user):
        user.press("ai:key")
        log = user.say("salah")
        assert {"kind": "delete", "text": "salah"} in log and "tidak valid" in user.texts(log)
        log = user.say("kunci-benar-1234")
        assert {"kind": "delete", "text": "kunci-benar-1234"} in log
        assert app_settings.get("GEMINI_API_KEY") == "kunci-benar-1234" and "…1234" in user.texts(log)

    def test_tes_ai(self, user, monkeypatch):
        monkeypatch.setattr(ai_helper, "ping", lambda: "OK")
        assert "✅ AI menjawab" in user.texts(user.press("ai:test"))

    def test_claude_login_belum_siap(self, user, monkeypatch):
        monkeypatch.setattr(ai_helper, "list_models", lambda name, key=None: (_ for _ in ()).throw(RuntimeError("belum login")))
        log = user.press("ai:prov:claude")
        assert "🔐 Login akun Claude Console|ai:auth:login" in user.buttons(log)
        assert "ant auth login --no-browser" in user.texts(user.press("ai:auth:login"))


class TestAkunGmail:
    def test_tambah_akun_setelah_login_berhasil(self, user, monkeypatch):
        monkeypatch.setattr(gmail_accounts, "verify", lambda address, password: None)
        user.press("acc:add")
        user.say("Kedua@Gmail.com")
        log = user.say("abcd efgh ijkl mnop")
        assert {"kind": "delete", "text": "abcd efgh ijkl mnop"} in log
        assert [(a.address, a.password) for a in gmail_accounts.load()] == [("kedua@gmail.com", "abcdefghijklmnop")]
        assert "password" not in str(user.context.user_data)

    def test_login_gagal_tidak_disimpan(self, user, monkeypatch):
        def verify(address, password):
            raise RuntimeError("AUTHENTICATIONFAILED")
        monkeypatch.setattr(gmail_accounts, "verify", verify)
        user.press("acc:add")
        user.say("kedua@gmail.com")
        assert "gagal" in user.texts(user.say("abcdefghijklmnop")) and gmail_accounts.load() == []


def test_menu_akun_ditolak_tanpa_allowed_user_ids(user):
    import bot
    allowed = set(bot.ALLOWED)
    bot.ALLOWED.clear()
    try:
        assert "Isi ALLOWED_USER_IDS" in user.texts(user.press("acc"))
    finally:
        bot.ALLOWED.update(allowed)
    assert bot.ALLOWED == {UID}
