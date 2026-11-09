import job_rules as rules
from conftest import weekday_at


class TestScam:
    def test_biaya_ktp_dan_wa_saja_adalah_tanda_serius(self):
        signals, serious = rules.scam_signals(
            "Dibutuhkan admin. Ada biaya pelatihan Rp250.000 dan wajib kirim foto KTP. Hubungi WA 0812", {})
        assert serious
        assert len(signals) == 3

    def test_biaya_yang_ditanggung_perusahaan_bukan_penipuan(self):
        text = "Biaya pelatihan ditanggung perusahaan. Tidak dipungut biaya. Kirim CV ke hr@maju.co.id"
        assert rules.scam_signals(text, {}) == ([], False)

    def test_transfer_knowledge_bukan_penipuan(self):
        assert rules.scam_signals("Transfer knowledge ke tim baru, kirim CV ke hr@a.com", {})[0] == []

    def test_pt_memakai_gmail_dan_tanda_dari_ai_perlu_dicek(self):
        signals, serious = rules.scam_signals("Kirim CV", {
            "perusahaan": "PT Maju Jaya", "email_hr": "rekrut.maju@gmail.com", "tanda_penipuan": ["Gaji tidak wajar"]})
        assert not serious
        assert any("gmail.com" in s for s in signals)
        assert "Gaji tidak wajar" in signals

    def test_modus_like_dibayar(self):
        assert rules.scam_signals("Kerja mudah: like video TikTok dibayar Rp50.000 per tugas", {})[1]


class TestPreferences:
    PREFS = {"lokasi": ["Malang"], "mode": "remote_hybrid", "gaji_min": 5_000_000, "hindari": ["sales"]}

    def test_baca_gaji(self):
        assert [rules.parse_salary(x) for x in ("5jt", "7,5 juta", "Rp 5.000.000", "5000000", "abc")] == \
            [5_000_000, 7_500_000, 5_000_000, 5_000_000, None]

    def test_kata_yang_dihindari(self):
        assert rules.pref_issues({"posisi": "Sales Executive", "lokasi": "Malang"}, self.PREFS) == \
            ['Posisi mengandung "sales" (kamu hindari)']

    def test_lokasi_mode_gaji_tidak_cocok(self):
        job = {"posisi": "Dev", "lokasi": "Jakarta", "mode_kerja": "wfo", "gaji_max": 4_000_000}
        assert len(rules.pref_issues(job, self.PREFS)) == 3

    def test_remote_lolos_filter_kota(self):
        assert rules.pref_issues({"posisi": "Dev", "lokasi": "Jakarta", "mode_kerja": "remote"}, self.PREFS) == []

    def test_info_yang_tidak_disebut_bukan_masalah(self):
        assert rules.pref_issues({"posisi": "Dev"}, self.PREFS) == []


class TestDuplicates:
    APPS = [{"id": 1, "perusahaan": "PT. Satubakery Indonesia", "posisi": "Backend Developer"},
            {"id": 2, "perusahaan": "Satubakery", "posisi": "Graphic Designer"}]

    def test_nama_perusahaan_beda_tulisan_posisi_mirip(self):
        assert [a["id"] for a in rules.find_duplicates(self.APPS, "Backend Developer (Laravel)", "Satubakery")] == [1]

    def test_perusahaan_lain_bukan_dobel(self):
        assert rules.find_duplicates(self.APPS, "Backend Developer", "PT Lain") == []


class TestSchedule:
    def test_waktu_kirim_berikutnya(self):
        assert rules.next_work_time(weekday_at(4, 23)) == weekday_at(7, 8)  # Jumat malam -> Senin
        assert rules.next_work_time(weekday_at(1, 6)) == weekday_at(1, 8)   # Selasa subuh -> Selasa 08.00
        assert rules.next_work_time(weekday_at(1, 10)) == weekday_at(2, 8)  # Selasa siang -> Rabu 08.00

    def test_jam_kerja(self):
        assert rules.in_work_hours(weekday_at(2, 9))
        assert not rules.in_work_hours(weekday_at(2, 17))
        assert not rules.in_work_hours(weekday_at(5, 10))  # Sabtu

    def test_label_waktu(self):
        assert rules.when_label(weekday_at(2, 8), weekday_at(1, 10)) == "besok jam 08:00"
        assert rules.when_label(weekday_at(7, 8), weekday_at(4, 23)).startswith("Senin")

    def test_hitung_kiriman_per_hari(self):
        apps = [{"dari": "a@gmail.com", "tanggal": "2026-11-10T09:00+07:00"},
                {"dari": "A@gmail.com", "tanggal": "2026-11-10T11:00+07:00"},
                {"dari": "b@gmail.com", "tanggal": "2026-11-10T11:00+07:00"},
                {"dari": "a@gmail.com", "tanggal": "2026-11-09T11:00+07:00"}]
        assert rules.sent_on(apps, "a@gmail.com", weekday_at(1, 10).date()) == 2
