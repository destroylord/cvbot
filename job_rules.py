"""Aturan lowongan tanpa AI dan tanpa Telegram: tanda penipuan, preferensi, lamaran dobel, jam kirim.

Semua fungsi murni supaya mudah diuji.
"""
import datetime as dt
import difflib
import re

# ---------------------------------------------------------------- tanda penipuan
# (pola, alasan, serius?) — serius = tanda klasik penipuan (meminta uang / data sensitif)
SCAM_PATTERNS = [
    (r"biaya\s+(pelatihan|training|administrasi|admin|pendaftaran|seragam|medical|medis|tes|psikotes|"
     r"akomodasi|tiket|pembuatan|registrasi|id card)", "Meminta biaya dari pelamar (perusahaan resmi tidak "
     "memungut biaya rekrutmen)", True),
    (r"(transfer\s+(uang|dana|biaya|ke\s+rekening|sejumlah)|uang\s+jaminan|deposit\s+(uang|sebesar|rp))",
     "Meminta transfer uang / deposit", True),
    (r"(kirim|lampirkan|foto)\s+(foto\s+)?(ktp|kartu keluarga|\bkk\b|buku tabungan|nomor rekening|kartu atm|"
     r"kode otp|\botp\b)", "Meminta dokumen/data pribadi sensitif sejak awal", True),
    (r"(dijamin\s+(diterima|lolos|langsung kerja)|pasti\s+diterima|tanpa\s+(tes|interview|wawancara)\s+langsung\s+"
     r"(kerja|diterima))", "Menjanjikan pasti diterima", False),
    (r"(kerja\s+(dari\s+)?rumah|kerja\s+online).{0,60}(gaji|penghasilan)\s+(jutaan|hingga|sampai)\s+\d+\s*(juta|jt)\s*"
     r"(/|per)\s*(hari|minggu)", "Gaji kerja online yang tidak wajar", False),
    (r"(like|follow|subscribe|rating|review)\s+(produk|video|akun).{0,40}(dibayar|komisi|gaji)",
     "Pola 'tugas like/review dibayar' (modus penipuan umum)", True),
]
# Kalau dekat kata-kata ini, pola biaya di atas bukan penipuan ("biaya pelatihan ditanggung perusahaan")
SAFE_CONTEXT = re.compile(r"ditanggung|gratis|free|tidak\s+dipungut|tanpa\s+biaya|dibayar\s+perusahaan|"
                          r"tidak\s+ada\s+biaya|reimburse", re.I)
FREE_EMAIL = re.compile(r"@(gmail|yahoo|ymail|hotmail|outlook|live|icloud|aol|proton(mail)?)\.", re.I)
WHATSAPP = re.compile(r"wa\.me/|whatsapp|\bwa\s*:|\bwa\s+(ke|aja|saja)|hubungi\s+wa|chat\s+wa", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def scam_signals(job_text: str, job: dict | None = None) -> tuple[list[str], bool]:
    """Tanda-tanda lowongan penipuan. Mengembalikan (daftar alasan, ada tanda serius?)."""
    text = " ".join(str(job_text or "").split())
    lower = text.lower()
    signals, serious = [], False
    for pattern, reason, is_serious in SCAM_PATTERNS:
        for match in re.finditer(pattern, lower):
            around = lower[max(0, match.start() - 40): match.end() + 60]
            if SAFE_CONTEXT.search(around):
                continue
            if reason not in signals:
                signals.append(reason)
                serious = serious or is_serious
            break
    job = job or {}
    company = str(job.get("perusahaan") or "")
    hr_email = str(job.get("email_hr") or "")
    if hr_email and FREE_EMAIL.search(hr_email) and re.match(r"(pt|cv)\b\.?", company.strip().lower()):
        signals.append(f"Email HR memakai email gratis ({hr_email.split('@')[-1]}) padahal atas nama {company}")
    if WHATSAPP.search(lower) and not EMAIL.search(text) and not hr_email:
        signals.append("Hanya bisa dihubungi lewat WhatsApp, tidak ada email atau situs resmi")
    for reason in job.get("tanda_penipuan") or []:  # dari AI
        if isinstance(reason, str) and reason.strip() and reason.strip() not in signals:
            signals.append(reason.strip())
    return signals, serious


# ---------------------------------------------------------------- preferensi lowongan
MODES = {"semua": "Semua", "remote": "Remote saja", "remote_hybrid": "Remote / hybrid"}


def parse_salary(text: str) -> int | None:
    """'5jt' / '5 juta' / '5.000.000' / '5000000' -> 5000000. None kalau tidak bisa dibaca."""
    t = str(text or "").lower().replace("rp", "").strip()
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(jt|juta)", t)
    if m:
        return int(float(m.group(1).replace(",", ".")) * 1_000_000)
    digits = re.sub(r"[.,\s]", "", t)
    return int(digits) if digits.isdigit() else None


def rupiah(n: int) -> str:
    return "Rp" + f"{n:,}".replace(",", ".")


def pref_issues(job: dict, prefs: dict | None) -> list[str]:
    """Alasan lowongan ini tidak sesuai preferensi pengguna. Info yang tidak diketahui tidak dianggap masalah."""
    prefs = prefs or {}
    issues = []
    title = str(job.get("posisi") or "").lower()
    for word in prefs.get("hindari") or []:
        if word.strip() and word.strip().lower() in title:
            issues.append(f"Posisi mengandung \"{word.strip()}\" (kamu hindari)")
            break
    mode = str(job.get("mode_kerja") or "").lower()
    want = prefs.get("mode") or "semua"
    if mode and want == "remote" and mode != "remote":
        issues.append(f"Kerja {mode}, kamu hanya mau remote")
    elif mode and want == "remote_hybrid" and mode not in ("remote", "hybrid"):
        issues.append(f"Kerja {mode}, kamu hanya mau remote/hybrid")
    places = [p.strip().lower() for p in prefs.get("lokasi") or [] if p.strip()]
    where = str(job.get("lokasi") or "").lower()
    if places and where and mode != "remote" and not any(p in where for p in places):
        issues.append(f"Lokasi {job.get('lokasi')} di luar preferensi")
    minimum = prefs.get("gaji_min")
    top = job.get("gaji_max") or job.get("gaji_min")
    if minimum and isinstance(top, (int, float)) and 0 < top < minimum:
        issues.append(f"Gaji sampai {rupiah(int(top))}, di bawah minimal kamu {rupiah(int(minimum))}")
    return issues


# ---------------------------------------------------------------- lamaran dobel
_COMPANY_NOISE = re.compile(r"\b(pt|cv|tbk|persero|ltd|inc|co|indonesia)\b|[^\w\s]")


def company_key(name: str) -> str:
    return " ".join(_COMPANY_NOISE.sub(" ", str(name or "").lower()).split())


def find_duplicates(apps: list[dict], posisi: str, perusahaan: str) -> list[dict]:
    """Lamaran sebelumnya ke perusahaan yang sama dengan posisi yang mirip (atau posisi tidak diketahui)."""
    key = company_key(perusahaan)
    if not key:
        return []
    title = str(posisi or "").lower()
    found = []
    for app in apps or []:
        if company_key(app.get("perusahaan")) != key:
            continue
        other = str(app.get("posisi") or "").lower()
        if not title or not other or difflib.SequenceMatcher(None, title, other).ratio() >= 0.6:
            found.append(app)
    return found


# ---------------------------------------------------------------- jam kirim & batas harian
WORK_START, WORK_END = 8, 17  # jam kerja Senin-Jumat


def in_work_hours(now: dt.datetime) -> bool:
    return now.weekday() < 5 and WORK_START <= now.hour < WORK_END


def next_work_time(now: dt.datetime) -> dt.datetime:
    """Jam 08:00 hari kerja berikutnya (atau hari ini kalau belum jam 8 di hari kerja)."""
    day = now.replace(hour=WORK_START, minute=0, second=0, microsecond=0)
    if now.weekday() < 5 and now < day:
        return day
    day += dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def sent_on(apps: list[dict], account: str, date: dt.date) -> int:
    """Jumlah lamaran yang dikirim dari akun ini pada tanggal tersebut."""
    count = 0
    for app in apps or []:
        if str(app.get("dari") or "").lower() != str(account).lower():
            continue
        try:
            if dt.datetime.fromisoformat(app.get("tanggal") or "").date() == date:
                count += 1
        except ValueError:
            continue
    return count


DAY_NAMES = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def when_label(when: dt.datetime, now: dt.datetime) -> str:
    if when.date() == now.date():
        day = "hari ini"
    elif when.date() == (now + dt.timedelta(days=1)).date():
        day = "besok"
    else:
        day = f"{DAY_NAMES[when.weekday()]} {when.strftime('%d-%m')}"
    return f"{day} jam {when.strftime('%H:%M')}"
