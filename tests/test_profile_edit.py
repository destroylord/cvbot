import copy

import pytest

import profile_edit as pe


def test_data_ai_yang_tidak_rapi_dirapikan():
    prof = {"skill": "Python\nSQL", "pengalaman": [{"posisi": "Dev", "poin": "x\n- y"}, "rusak"], "proyek": None}
    assert pe.list_items(prof, "skill") == ["Python", "SQL"]
    assert pe.entries(prof, "pengalaman")[0]["poin"] == ["x", "y"]
    assert len(prof["pengalaman"]) == 1


def test_tambah_daftar_abaikan_duplikat_dan_kosong():
    prof = {"skill": ["Python", "SQL"]}
    assert pe.list_add(prof, "skill", ["sql", "Docker", " ", "Docker"]) == 1
    assert prof["skill"] == ["Python", "SQL", "Docker"]


def test_naikkan_urutan():
    prof = {"skill": ["a", "b", "c"]}
    pe.list_move_up(prof, "skill", 2)
    pe.list_move_up(prof, "skill", 0)
    assert prof["skill"] == ["a", "c", "b"]


def test_entri_baru_di_atas():
    prof = {"pengalaman": [{"posisi": "Lama", "poin": []}]}
    assert pe.entry_add(prof, "pengalaman", {"posisi": " Lead ", "perusahaan": "", "poin": ["a", ""]}) == 0
    assert prof["pengalaman"][0] == {"posisi": "Lead", "perusahaan": None, "poin": ["a"]}


def test_field_yang_tidak_ada_ditolak():
    with pytest.raises(KeyError):
        pe.entry_set_field({"proyek": [{"nama": "x"}]}, "proyek", 0, "perusahaan", "x")


class TestMergePoints:
    PROF = {"pengalaman": [{"posisi": "Dev", "perusahaan": "PT A", "periode": "2022",
                            "poin": ["Membuat API untuk 50.000 pengguna", "Membuat sistem kasir"]},
                           {"posisi": "Intern", "perusahaan": "PT B", "poin": ["Membantu tim QA"]}]}

    def test_angka_karangan_dikembalikan_ke_asli(self):
        facts = [{"poin": "Membuat sistem kasir", "hasil": "antre turun dari 10 jadi 3 menit"}]
        new, changes, rejected = pe.merge_points(self.PROF, {"pengalaman": [
            ["API melayani 50000 pengguna", "Sistem kasir: antre turun dari 10 jadi 3 menit"],
            ["Membantu QA sehingga bug turun 40%"]]}, facts)
        assert new["pengalaman"][0]["poin"] == ["API melayani 50000 pengguna", "Sistem kasir: antre turun dari 10 jadi 3 menit"]
        assert new["pengalaman"][1]["poin"] == ["Membantu tim QA"]
        assert rejected == ["Membantu QA sehingga bug turun 40%"]
        assert [(c["sec"], c["i"]) for c in changes] == [("pengalaman", 0)]

    def test_profil_asli_tidak_berubah(self):
        before = copy.deepcopy(self.PROF)
        pe.merge_points(self.PROF, {"pengalaman": [["baru"], ["baru"]]}, [])
        assert self.PROF == before

    def test_isian_rusak_diabaikan(self):
        new, changes, _ = pe.merge_points(self.PROF, {"pengalaman": [[], "rusak", [["x"]]]}, [])
        assert changes == [] and new["pengalaman"] == self.PROF["pengalaman"]
