"""Semua yang berhubungan dengan AI: parsing CV, analisis lowongan, tailoring CV, cover letter.

Provider AI dipilih lewat AI_PROVIDER di .env:
  gemini -> Google Gemini API (ada kuota gratis, key dari https://aistudio.google.com)
  ollama -> model lokal gratis di komputer sendiri (https://ollama.com)
  claude -> Claude API (berbayar, key dari https://console.anthropic.com)
"""
import base64
import json
import os
import re

import requests


def _ask(system: str, prompt: str, max_tokens: int = 2000, image: tuple[bytes, str] | None = None) -> str:
    """image: (isi file, mime type), misalnya poster lowongan."""
    provider = os.getenv("AI_PROVIDER", "gemini").strip().lower()
    if provider == "gemini":
        text = _ask_gemini(system, prompt, image)
    elif provider == "ollama":
        text = _ask_ollama(system, prompt, image)
    elif provider == "claude":
        text = _ask_claude(system, prompt, max_tokens, image)
    else:
        raise ValueError(f"AI_PROVIDER tidak dikenal: {provider} (pilih gemini, ollama, atau claude)")
    # Beberapa model lokal menulis proses berpikirnya di <think>...</think>, buang bagian itu
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _ask_gemini(system: str, prompt: str, image: tuple[bytes, str] | None = None) -> str:
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    parts = [{"text": prompt}]
    if image:
        parts.insert(0, {"inline_data": {"mime_type": image[1], "data": base64.b64encode(image[0]).decode()}})
    resp = requests.post(
        url,
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
        },
        timeout=180,
    )
    if resp.status_code == 429:
        raise RuntimeError("Kuota gratis Gemini habis untuk sementara. Tunggu sebentar lalu coba lagi.")
    if not resp.ok:
        raise RuntimeError(f"Gemini error {resp.status_code}: {resp.text[:300]}")
    candidates = resp.json().get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini tidak memberi jawaban (mungkin terkena filter). Coba lagi.")
    parts = candidates[0].get("content", {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _ask_ollama(system: str, prompt: str, image: tuple[bytes, str] | None = None) -> str:
    base = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    user_msg = {"role": "user", "content": prompt}
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    if image:
        # Model teks biasa tidak bisa membaca gambar, pakai model vision
        model = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
        user_msg["images"] = [base64.b64encode(image[0]).decode()]
    resp = requests.post(
        f"{base}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, user_msg],
            "stream": False,
            "options": {"num_ctx": 16384},
        },
        timeout=600,  # model lokal bisa lambat
    )
    if not resp.ok:
        raise RuntimeError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["message"]["content"]


_claude_client = None


def _ask_claude(system: str, prompt: str, max_tokens: int, image: tuple[bytes, str] | None = None) -> str:
    global _claude_client
    from anthropic import Anthropic  # hanya di-import kalau memang pakai Claude
    if _claude_client is None:
        _claude_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    content = [{"type": "text", "text": prompt}]
    if image:
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": image[1], "data": base64.b64encode(image[0]).decode()}})
    resp = _claude_client.messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def _parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    return json.loads(match.group(0) if match else text)


JSON_ONLY = "Balas HANYA dengan JSON valid, tanpa teks lain."


# ---------------------------------------------------------------- CV
def extract_profile(cv_text: str) -> dict:
    prompt = f"""Ubah CV berikut menjadi JSON dengan key:
nama, email, telepon, lokasi, tautan (list: LinkedIn/portfolio/GitHub),
ringkasan (2-3 kalimat), skill (list string),
pengalaman (list objek: posisi, perusahaan, periode, poin (list string, salin isi aslinya)),
proyek (list objek: nama, poin (list string)),
pendidikan (list string), sertifikasi (list string), bahasa (list string).
Isi null / list kosong jika tidak ada. Jangan mengarang data apa pun.

CV:
{cv_text[:12000]}"""
    return _parse_json(_ask("Kamu adalah parser CV. " + JSON_ONLY, prompt, max_tokens=3000))


# ---------------------------------------------------------------- lowongan
def analyze_job(profile: dict, job_text: str) -> dict:
    prompt = f"""Bandingkan profil kandidat dengan teks lowongan di bawah.

Jika teks BUKAN lowongan kerja (misalnya halaman login atau error), balas: {{"bukan_lowongan": true}}

Jika lowongan, balas JSON dengan key:
posisi, perusahaan, email_hr (email untuk melamar jika tertulis, kalau tidak null),
bahasa_lowongan ("id" atau "en"), skor (0-100, seberapa cocok kandidat),
skill_cocok (list), skill_kurang (list), alasan (1-2 kalimat, bahasa Indonesia).
Nilai dengan jujur, jangan terlalu murah hati.

PROFIL KANDIDAT:
{json.dumps(profile, ensure_ascii=False)}

TEKS LOWONGAN:
{job_text[:8000]}"""
    return _parse_json(_ask("Kamu adalah rekruter berpengalaman. " + JSON_ONLY, prompt))


def read_job_image(image: bytes, mime: str, caption: str = "") -> str:
    """Salin isi poster/screenshot lowongan menjadi teks, supaya bisa diproses seperti lowongan biasa."""
    prompt = """Salin SEMUA teks di gambar lowongan kerja ini apa adanya: posisi, perusahaan,
kualifikasi, jobdesk, lokasi, gaji, tenggat, dan terutama alamat email / cara melamar
(salin alamat email persis, huruf demi huruf). Jangan menambah info yang tidak ada di gambar.
Jika gambar bukan lowongan kerja, balas persis: BUKAN_LOWONGAN"""
    if caption:
        prompt += f"\n\nKeterangan yang dikirim bersama gambar:\n{caption[:3000]}"
    text = _ask("Kamu menyalin teks dari gambar dengan teliti.", prompt, max_tokens=3000, image=(image, mime))
    if caption and caption not in text:
        text += f"\n\nKeterangan tambahan:\n{caption}"
    return text


def extract_and_rank(profile: dict, email_text: str, sender: str) -> list[dict]:
    """Ambil daftar lowongan dari satu email job alert sekaligus beri skor."""
    prompt = f"""Berikut isi email job alert dari: {sender}
Link di email ditandai seperti [L12].

Ambil SEMUA lowongan kerja di email ini. Untuk tiap lowongan, beri skor kecocokan
(0-100) dengan profil kandidat berdasarkan info yang ada (judul, perusahaan, ringkasan).
Nilai dengan jujur.

Balas JSON:
{{"lowongan": [{{"posisi": "...", "perusahaan": "...", "lokasi": "... atau null",
"link_ref": "L12 (link ke halaman lowongan itu) atau null", "skor": 0, "alasan": "1 kalimat bahasa Indonesia"}}]}}
Jika email tidak berisi lowongan, balas {{"lowongan": []}}.

PROFIL KANDIDAT:
{json.dumps(profile, ensure_ascii=False)}

ISI EMAIL:
{email_text[:14000]}"""
    return _parse_json(_ask(JSON_ONLY, prompt, max_tokens=3000)).get("lowongan") or []


# ---------------------------------------------------------------- tailoring CV
def tailor_cv(profile: dict, job_text: str, lang: str | None) -> dict:
    language = "Bahasa Inggris" if lang == "en" else "Bahasa Indonesia"
    system = ("Kamu adalah penulis CV profesional yang SANGAT JUJUR. Kamu hanya boleh memakai "
              "fakta yang ada di CV asli. " + JSON_ONLY)
    prompt = f"""Sesuaikan CV kandidat untuk lowongan di bawah supaya lebih relevan dan ramah ATS.

BOLEH:
- menulis ulang ringkasan khusus untuk posisi ini
- mengurutkan ulang skill, pengalaman, dan poin supaya yang paling relevan ada di atas
- menulis ulang poin pengalaman dengan kata kerja aktif dan istilah dari lowongan,
  SELAMA maknanya sama persis dengan poin aslinya
- menghapus poin yang tidak relevan (setiap pengalaman tetap minimal 1 poin)
- menerjemahkan jabatan dan poin ke {language}

DILARANG:
- menambah skill, tools, sertifikasi, pengalaman, proyek, angka, atau pencapaian yang tidak ada di CV asli
- mengubah nama perusahaan, periode, atau pendidikan
- mengubah atau menerjemahkan nama skill (salin persis dari CV asli)

Tulis isi CV dalam {language}.

Balas JSON:
{{"profile": <objek dengan skema yang sama persis dengan profil asli>,
"perubahan": [3-6 poin singkat bahasa Indonesia tentang apa yang kamu ubah],
"saran_belajar": [skill penting dari lowongan yang BELUM dimiliki kandidat]}}

PROFIL ASLI:
{json.dumps(profile, ensure_ascii=False)}

LOWONGAN:
{job_text[:7000]}"""
    return _parse_json(_ask(system, prompt, max_tokens=4000))


def _norm(value) -> str:
    return str(value or "").strip().lower()


def guard_profile(original: dict, tailored: dict) -> dict:
    """Pengaman tambahan: buang apa pun yang tidak ada di CV asli, kembalikan data faktual."""
    result = dict(tailored)

    # Data pribadi, pendidikan, sertifikasi, bahasa selalu dari CV asli
    for key in ("nama", "email", "telepon", "lokasi", "tautan", "pendidikan", "sertifikasi", "bahasa"):
        result[key] = original.get(key)

    # Skill: hanya yang memang ada di CV asli
    orig_skills = {_norm(s) for s in original.get("skill") or []}
    skills = [s for s in tailored.get("skill") or [] if _norm(s) in orig_skills]
    result["skill"] = skills or original.get("skill") or []

    # Pengalaman: hanya perusahaan yang ada di CV asli, periode dikembalikan ke aslinya
    periods = {}
    for exp in original.get("pengalaman") or []:
        periods.setdefault(_norm(exp.get("perusahaan")), []).append(exp.get("periode"))
    experiences = []
    for exp in tailored.get("pengalaman") or []:
        key = _norm(exp.get("perusahaan"))
        if key in periods:
            exp = dict(exp)
            if periods[key]:
                exp["periode"] = periods[key].pop(0)
            experiences.append(exp)
    result["pengalaman"] = experiences or original.get("pengalaman") or []

    # Proyek: hanya yang namanya ada di CV asli
    orig_projects = {_norm(p.get("nama")) for p in original.get("proyek") or []}
    result["proyek"] = [p for p in tailored.get("proyek") or [] if _norm(p.get("nama")) in orig_projects]
    return result


# ---------------------------------------------------------------- cover letter
GAYA = {
    "formal": "Nada formal, sopan, dan profesional. Panjang 250-300 kata.",
    "santai": "Nada profesional tapi hangat dan luwes (tidak kaku), tetap sopan. Panjang 200-250 kata.",
    "singkat": "Singkat dan padat, langsung ke poin. Maksimal 150 kata.",
}


def write_cover_letter(profile: dict, job: dict, job_text: str, gaya: str = "formal") -> str:
    lang = "Bahasa Indonesia" if job.get("bahasa_lowongan") == "id" else "English"
    system = (
        "Kamu menulis cover letter yang personal, spesifik, dan jujur. "
        "Jangan mengarang pengalaman atau skill yang tidak ada di profil. "
        "Tulis dalam teks biasa tanpa markdown, siap dikirim sebagai isi email."
    )
    prompt = f"""Tulis cover letter dalam {lang} untuk posisi {job.get('posisi')} di {job.get('perusahaan')}.

Gaya: {GAYA.get(gaya, GAYA['formal'])}

Aturan:
- Buka langsung dengan alasan tertarik + nilai yang dibawa kandidat (hindari pembuka klise).
- Sebutkan 2-3 pengalaman/skill paling relevan dengan lowongan, pakai angka jika ada di profil.
- Tutup dengan ajakan interview yang sopan, lalu tanda tangan dengan nama & kontak kandidat.
- Jangan pakai placeholder seperti [Nama Perusahaan].

PROFIL:
{json.dumps(profile, ensure_ascii=False)}

LOWONGAN:
{job_text[:6000]}"""
    return _ask(system, prompt, max_tokens=1500)


def revise_text(text: str, instruction: str, kind: str = "cover letter") -> str:
    system = (f"Kamu merevisi {kind}. Jangan menambah fakta baru tentang kandidat. "
              f"Balas hanya dengan {kind} hasil revisi, teks biasa tanpa markdown.")
    return _ask(system, f"Instruksi revisi: {instruction}\n\n{kind}:\n{text}", max_tokens=1500)


# ---------------------------------------------------------------- follow-up & balasan HR
def write_followup(profile: dict, app: dict, days: int) -> str:
    lang = "English" if app.get("bahasa") == "en" else "Bahasa Indonesia"
    system = ("Kamu menulis email follow-up lamaran kerja yang sopan, singkat, dan tidak memaksa. "
              "Jangan mengarang fakta. Teks biasa tanpa markdown, tanpa baris subjek.")
    prompt = f"""Tulis email follow-up dalam {lang}, 80-130 kata.

Konteks: kandidat melamar posisi {app.get('posisi')} di {app.get('perusahaan')} {days} hari lalu
dan belum mendapat kabar.

Isi:
- sapaan sopan ke tim rekrutmen
- sebut posisi yang dilamar dan kapan kira-kira mengirim lamaran
- tegaskan minat + satu kekuatan kandidat yang relevan (dari profil)
- tanyakan dengan sopan perkembangan proses seleksi
- terima kasih, lalu tanda tangan dengan nama dan nomor telepon kandidat

PROFIL:
{json.dumps(profile, ensure_ascii=False)}"""
    return _ask(system, prompt, max_tokens=800)


def classify_reply(app: dict, sender: str, subject: str, text: str) -> dict:
    prompt = f"""Kandidat melamar posisi {app.get('posisi')} di {app.get('perusahaan')}.
Berikut email yang masuk dari pihak perusahaan. Klasifikasikan.

jenis (pilih satu):
- "interview": undangan interview, tes, psikotes, assessment, atau tahap seleksi berikutnya
- "ditolak": lamaran tidak dilanjutkan
- "diterima": tawaran kerja / offering letter
- "info": balasan manusia lain, misalnya meminta dokumen atau informasi tambahan
- "otomatis": konfirmasi otomatis bahwa lamaran sudah diterima sistem
- "tidak_terkait": newsletter, promosi, atau email yang tidak berhubungan dengan lamaran ini

Balas JSON:
{{"jenis": "...", "ringkasan": "1-2 kalimat bahasa Indonesia",
"jadwal": "tanggal/jam interview atau tenggat jika disebut, kalau tidak null",
"perlu_tindakan": "hal yang harus dilakukan kandidat jika ada, kalau tidak null"}}

DARI: {sender}
SUBJEK: {subject}
ISI:
{text[:5000]}"""
    return _parse_json(_ask(JSON_ONLY, prompt, max_tokens=800))
