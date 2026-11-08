"""Data lamaran: status, pengingat follow-up, dan statistik."""
import datetime as dt
from zoneinfo import ZoneInfo

STATUS = {
    "terkirim": "📤 Terkirim",
    "dibalas": "💬 Dibalas",
    "interview": "🎤 Interview",
    "ditolak": "❌ Ditolak",
    "diterima": "🎉 Diterima",
    "tidak_ada_kabar": "👻 Tidak ada kabar",
}
# Status yang masih perlu dicek balasannya di Gmail
CHECK_REPLIES = {"terkirim", "dibalas", "interview", "tidak_ada_kabar"}
RANK = {"terkirim": 0, "tidak_ada_kabar": 0, "dibalas": 1, "interview": 2, "ditolak": 3, "diterima": 3}
REPLY_TO_STATUS = {"interview": "interview", "ditolak": "ditolak", "diterima": "diterima", "info": "dibalas"}


def now(tz: str) -> dt.datetime:
    return dt.datetime.now(ZoneInfo(tz))


def sent_at(app: dict, tz: str) -> dt.datetime:
    try:
        value = dt.datetime.fromisoformat(app["tanggal"])
    except (KeyError, ValueError):
        return now(tz)
    return value if value.tzinfo else value.replace(tzinfo=ZoneInfo(tz))


def days_since(app: dict, tz: str) -> int:
    return max(0, (now(tz) - sent_at(app, tz)).days)


def migrate(data: dict, tz: str) -> None:
    """Ubah format 'riwayat' lama menjadi 'lamaran'."""
    if "lamaran" not in data:
        data["lamaran"] = []
        for i, old in enumerate(data.pop("riwayat", []) or [], 1):
            try:
                tanggal = dt.datetime.strptime(old.get("tanggal", ""), "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(tz))
            except ValueError:
                tanggal = now(tz)
            data["lamaran"].append({
                "id": i, "posisi": old.get("posisi"), "perusahaan": old.get("perusahaan"),
                "ke": old.get("ke"), "skor": old.get("skor"), "cv_disesuaikan": old.get("cv_disesuaikan", False),
                "tanggal": tanggal.isoformat(timespec="minutes"), "status": "terkirim",
                "riwayat_status": [], "followups": [], "balasan": [], "reminder_sent": False,
            })
    data.setdefault("next_id", max((a["id"] for a in data["lamaran"]), default=0) + 1)


def add_application(data: dict, info: dict, tz: str) -> dict:
    app = {
        "id": data["next_id"], "tanggal": now(tz).isoformat(timespec="minutes"), "status": "terkirim",
        "riwayat_status": [], "followups": [], "balasan": [], "reminder_sent": False, **info,
    }
    data["next_id"] += 1
    data["lamaran"].append(app)
    return app


def find(data: dict, app_id: int) -> dict | None:
    return next((a for a in data.get("lamaran") or [] if a["id"] == app_id), None)


def set_status(app: dict, status: str, tz: str) -> bool:
    if status not in STATUS or app.get("status") == status:
        return False
    app["status"] = status
    app.setdefault("riwayat_status", []).append({"status": status, "tanggal": now(tz).isoformat(timespec="minutes")})
    return True


def apply_reply_status(app: dict, jenis: str, tz: str) -> bool:
    """Naikkan status otomatis berdasarkan jenis balasan HR (tidak pernah menurunkan)."""
    new = REPLY_TO_STATUS.get(jenis)
    if not new or RANK[new] <= RANK.get(app.get("status"), 0):
        return False
    return set_status(app, new, tz)


def pop_due_reminders(data: dict, days: int, tz: str) -> list[dict]:
    due = []
    for app in data.get("lamaran") or []:
        if app.get("status") == "terkirim" and not app.get("reminder_sent") and days_since(app, tz) >= days:
            app["reminder_sent"] = True
            due.append(dict(app))
    return due


def stats_text(data: dict, tz: str) -> str:
    apps = data.get("lamaran") or []
    if not apps:
        return "Belum ada lamaran yang terkirim, jadi belum ada statistik."
    total = len(apps)
    count = {k: sum(1 for a in apps if a.get("status") == k) for k in STATUS}
    responded = [a for a in apps if a.get("status") in ("dibalas", "interview", "ditolak", "diterima")]
    interviews = count["interview"] + count["diterima"]

    def pct(n, d):
        return f"{round(100 * n / d)}%" if d else "-"

    scores = [a["skor"] for a in apps if isinstance(a.get("skor"), (int, float))]
    tailored = [a for a in apps if a.get("cv_disesuaikan")]
    original = [a for a in apps if not a.get("cv_disesuaikan")]
    t_resp = sum(1 for a in tailored if a in responded)
    o_resp = sum(1 for a in original if a in responded)

    weeks = []
    for w in range(4):
        n = sum(1 for a in apps if 7 * w <= days_since(a, tz) < 7 * (w + 1))
        label = "Minggu ini" if w == 0 else f"{w} minggu lalu"
        weeks.append(f"  {label}: {n}")

    status_lines = "\n".join(f"  {label}: {count[k]}" for k, label in STATUS.items() if count[k])
    return (
        f"📊 Statistik lamaran\n\n"
        f"Total terkirim: {total}\n"
        f"Tingkat respons: {pct(len(responded), total)} ({len(responded)} dari {total})\n"
        f"Sampai tahap interview: {pct(interviews, total)}\n"
        f"Rata-rata skor kecocokan: {round(sum(scores) / len(scores)) if scores else '-'}\n\n"
        f"Per status:\n{status_lines}\n\n"
        f"Respons per jenis CV:\n"
        f"  CV disesuaikan: {pct(t_resp, len(tailored))} ({t_resp}/{len(tailored)})\n"
        f"  CV asli: {pct(o_resp, len(original))} ({o_resp}/{len(original)})\n\n"
        f"Lamaran per minggu:\n" + "\n".join(weeks)
    )
