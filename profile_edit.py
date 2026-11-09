"""Mengubah profil CV asli (hasil scan CV) dari menu 📄 CV Saya.

Fungsi murni: menerima dan mengubah dict profil, tanpa Telegram. Profil hasil AI kadang berisi
string atau null di field yang seharusnya list, jadi semua akses lewat _items / _entries.
"""

FIELDS = {"nama": "Nama", "email": "Email", "telepon": "Telepon", "lokasi": "Lokasi", "ringkasan": "Ringkasan"}

# Bagian berupa daftar teks
LISTS = {
    "skill": "🛠️ Skill", "pendidikan": "🎓 Pendidikan", "sertifikasi": "📜 Sertifikasi",
    "bahasa": "🌐 Bahasa", "tautan": "🔗 Tautan",
}

# Bagian berupa entri dengan poin-poin
ENTRIES = {
    "pengalaman": {"label": "💼 Pengalaman", "fields": {"posisi": "Posisi", "perusahaan": "Perusahaan",
                                                       "periode": "Periode"}},
    "proyek": {"label": "🧩 Proyek", "fields": {"nama": "Nama proyek"}},
}


def _split(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [line.strip(" -•\t") for line in value.splitlines() if line.strip(" -•\t")]
    return [str(v).strip() for v in value if str(v or "").strip()]


def _items(profile: dict, sec: str) -> list[str]:
    profile[sec] = _split(profile.get(sec))
    return profile[sec]


def _entries(profile: dict, sec: str) -> list[dict]:
    entries = [e for e in profile.get(sec) or [] if isinstance(e, dict)]
    for e in entries:
        e["poin"] = _split(e.get("poin"))
    profile[sec] = entries
    return entries


def _move_up(items: list, i: int) -> None:
    if 0 < i < len(items):
        items[i - 1], items[i] = items[i], items[i - 1]


# ---------------------------------------------------------------- field tunggal
def set_field(profile: dict, field: str, value: str) -> None:
    if field not in FIELDS:
        raise KeyError(field)
    profile[field] = value.strip() or None


# ---------------------------------------------------------------- daftar teks
def list_items(profile: dict, sec: str) -> list[str]:
    return list(_items(profile, sec))


def list_add(profile: dict, sec: str, values: list[str]) -> int:
    """Tambah item (yang sudah ada diabaikan). Mengembalikan jumlah yang benar-benar ditambahkan."""
    items = _items(profile, sec)
    existing = {v.lower() for v in items}
    added = [v for v in dict.fromkeys(v.strip() for v in values) if v and v.lower() not in existing]
    items.extend(added)
    return len(added)


def list_set(profile: dict, sec: str, i: int, value: str) -> None:
    _items(profile, sec)[i] = value.strip()


def list_delete(profile: dict, sec: str, i: int) -> str:
    return _items(profile, sec).pop(i)


def list_move_up(profile: dict, sec: str, i: int) -> None:
    _move_up(_items(profile, sec), i)


# ---------------------------------------------------------------- entri (pengalaman / proyek)
def entries(profile: dict, sec: str) -> list[dict]:
    return _entries(profile, sec)


def entry_title(sec: str, entry: dict) -> str:
    if sec == "pengalaman":
        title = " - ".join(x for x in (entry.get("posisi"), entry.get("perusahaan")) if x) or "(tanpa judul)"
        return f"{title} ({entry['periode']})" if entry.get("periode") else title
    return entry.get("nama") or "(tanpa nama)"


def entry_add(profile: dict, sec: str, entry: dict, on_top: bool = True) -> int:
    """Pengalaman baru biasanya yang terbaru, jadi ditaruh paling atas. Mengembalikan indeksnya."""
    clean = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in entry.items()}
    clean["poin"] = _split(entry.get("poin"))
    items = _entries(profile, sec)
    if on_top:
        items.insert(0, clean)
        return 0
    items.append(clean)
    return len(items) - 1


def entry_set_field(profile: dict, sec: str, i: int, field: str, value: str) -> None:
    if field not in ENTRIES[sec]["fields"]:
        raise KeyError(field)
    _entries(profile, sec)[i][field] = value.strip() or None


def entry_delete(profile: dict, sec: str, i: int) -> dict:
    return _entries(profile, sec).pop(i)


def entry_move_up(profile: dict, sec: str, i: int) -> None:
    _move_up(_entries(profile, sec), i)


def point_add(profile: dict, sec: str, i: int, texts: list[str]) -> int:
    new = _split(texts)
    _entries(profile, sec)[i]["poin"].extend(new)
    return len(new)


def point_set(profile: dict, sec: str, i: int, j: int, text: str) -> None:
    _entries(profile, sec)[i]["poin"][j] = text.strip()


def point_delete(profile: dict, sec: str, i: int, j: int) -> str:
    return _entries(profile, sec)[i]["poin"].pop(j)


def point_move_up(profile: dict, sec: str, i: int, j: int) -> None:
    _move_up(_entries(profile, sec)[i]["poin"], j)
