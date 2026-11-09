"""Penyimpanan, akun Gmail, CV (baca/tulis/font), dan pembacaan email job alert."""
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pytest

import cv_builder
import cv_fonts
import cv_parser
import gmail_accounts
import job_alerts
import storage


class TestStorage:
    def test_file_rusak_dipulihkan_dan_disalin(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text('{"a": 1}ODEL": "sisa tulisan lama"}', encoding="utf-8")
        assert storage.read_json(path, {}) == {"a": 1}
        assert json.loads(path.read_text()) == {"a": 1}
        assert (tmp_path / "settings.json.rusak").exists()

    def test_tulis_atomik_tanpa_sisa(self, tmp_path):
        storage.write_json(tmp_path / "x.json", {"b": 2}, private=True)
        assert storage.read_json(tmp_path / "x.json", {}) == {"b": 2}
        assert not list(tmp_path.glob(".x.json.*.tmp"))

    def test_file_kosong_dan_hancur(self, tmp_path):
        (tmp_path / "kosong.json").write_text("")
        assert storage.read_json(tmp_path / "kosong.json", {"d": 1}) == {"d": 1}
        (tmp_path / "hancur.json").write_text("{tidak json")
        with pytest.raises(RuntimeError, match="rusak"):
            storage.read_json(tmp_path / "hancur.json", {})


class TestGmailAccounts:
    def test_env_dan_bot_digabung_tanpa_duplikat(self, monkeypatch):
        monkeypatch.setenv("GMAIL_ADDRESS", "a@gmail.com")
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh")
        gmail_accounts.save("b@gmail.com", "x")
        accounts = gmail_accounts.load()
        assert [a.address for a in accounts] == ["a@gmail.com", "b@gmail.com"]
        assert accounts[0].password == "abcdefgh" and accounts[0].from_env

    def test_akun_env_tidak_bisa_dihapus_dari_bot(self, monkeypatch):
        monkeypatch.setenv("GMAIL_ADDRESS", "a@gmail.com")
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "p")
        with pytest.raises(ValueError):
            gmail_accounts.remove("a@gmail.com")

    def test_ganti_password_tidak_mengubah_urutan(self):
        gmail_accounts.save("b@gmail.com", "1")
        gmail_accounts.save("c@gmail.com", "2")
        gmail_accounts.save("b@gmail.com", "baru")
        assert [(a.address, a.password) for a in gmail_accounts.load()] == [("b@gmail.com", "baru"), ("c@gmail.com", "2")]


PROFILE = {"nama": "Siti", "email": "siti@mail.com", "ringkasan": "Analis “SQL” & <Python> 🚀",
           "pengalaman": [{"posisi": "Analyst", "perusahaan": "PT Angka", "periode": "2021", "poin": "Dashboard\n- Laporan"}],
           "skill": ["SQL"], "pendidikan": ["S1 Statistika"], "bahasa": "Indonesia\nInggris"}


class TestCv:
    def test_pdf_dari_profil_berantakan_dan_dibaca_lagi(self, tmp_path):
        path = tmp_path / "cv.pdf"
        assert cv_builder.build_pdf(PROFILE, str(path), "en", "Helvetica") == "Helvetica"
        text = cv_parser.extract_text(str(path))
        assert "WORK EXPERIENCE" in text and "& <Python>" in text and "Laporan" in text and "Inggris" in text
        assert "🚀" not in text

    def test_docx_header_tabel_dan_font(self, tmp_path):
        from docx import Document
        doc = Document()
        doc.sections[0].header.paragraphs[0].text = "Siti | siti@mail.com"
        doc.add_paragraph("Judul").runs[0].font.name = "Georgia"
        for _ in range(3):
            doc.add_paragraph().add_run("Isi CV panjang dengan font Calibri. ").font.name = "Calibri"
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Skill", "SQL, Tableau"
        doc.save(tmp_path / "cv.docx")
        text = cv_parser.extract_text(str(tmp_path / "cv.docx"))
        assert text.startswith("Siti") and "SQL, Tableau" in text
        assert cv_parser.detect_font(str(tmp_path / "cv.docx")) == "Calibri"

    def test_format_lain_ditolak(self):
        with pytest.raises(ValueError):
            cv_parser.extract_text("cv.txt")

    @pytest.mark.parametrize("raw, family", [
        ("ABCDEF+Calibri-Bold", "Carlito"), ("TimesNewRomanPSMT", "Tinos"), ("ArialMT", "Arimo"),
        ("QWERTY+Poppins-Regular", "Poppins"), ("OpenSans-Regular", "Open Sans"), ("Aptos", "Carlito"),
        ("Helvetica", "Helvetica"), (None, "Carlito")])
    def test_padanan_font(self, raw, family):
        assert cv_fonts.resolve(raw) == family


class TestJobAlerts:
    def test_link_jadi_penanda_dan_unsubscribe_dibuang(self):
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText('<a href="https://js.com/1"><b>Backend Developer</b></a> '
                            '<a href="https://js.com/2">Customer Support Specialist</a> '
                            '<a href="https://js.com/1">Lihat</a> <a href="https://js.com/u">Unsubscribe</a>', "html"))
        text, links = job_alerts._message_to_text(msg)
        assert "Backend Developer [L1]" in text and "Customer Support Specialist [L2]" in text and "Lihat [L1]" in text
        assert links == {"L1": "https://js.com/1", "L2": "https://js.com/2"}

    def test_url_di_akhir_kalimat(self):
        msg = MIMEText("Lowongan: https://glints.com/a dan https://glints.com/b.", "plain")
        text, links = job_alerts._message_to_text(msg)
        assert text == "Lowongan: [L1] dan [L2]." and links["L2"] == "https://glints.com/b"

    def test_imap_read_only_dan_peek(self, monkeypatch):
        calls = []
        alert = MIMEText('<a href="https://x/1">Dev</a>', "html")
        alert["From"], alert["Message-ID"] = "JobStreet <a@jobstreet.com>", "<a1>"

        class FakeIMAP:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def login(self, user, pw):
                calls.append(("login", user))

            def select(self, box, readonly=False):
                calls.append(("select", readonly))

            def search(self, charset, *criteria):
                return "OK", [b"1" if "jobstreet" in criteria[-1] else b""]

            def fetch(self, num, what):
                calls.append(("fetch", what))
                return "OK", [(b"1", alert.as_bytes())]

        monkeypatch.setattr(job_alerts.imaplib, "IMAP4_SSL", FakeIMAP)
        monkeypatch.setenv("ALERT_SENDERS", "jobstreet,glints")
        emails = job_alerts.fetch_alert_emails(set(), 3, gmail_accounts.Account("b@gmail.com", "p"))
        assert [e["id"] for e in emails] == ["<a1>"]
        assert ("select", True) in calls and ("fetch", "(BODY.PEEK[])") in calls
        assert job_alerts.fetch_alert_emails({"<a1>"}, 3, gmail_accounts.Account("b@gmail.com", "p")) == []
