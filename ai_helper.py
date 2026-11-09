"""Semua yang berhubungan dengan AI: parsing CV, analisis lowongan, tailoring CV, cover letter.

Provider, model, dan API key diatur dari bot (⚙️ Pengaturan > 🤖 Model AI), atau dari .env:
  gemini -> Google Gemini API (ada kuota gratis)
  openai -> API apa pun yang kompatibel dengan OpenAI (OpenRouter, Groq, DeepSeek, LM Studio, dll.)
  claude -> Claude API (berbayar), dengan API key atau login akun Claude Console (ant auth login)
  ollama -> model lokal gratis di komputer sendiri
"""
import base64
import json
import logging
import os
import re
import time

import requests

import app_settings

log = logging.getLogger("cvbot.ai")

DEFAULTS = {
    "AI_PROVIDER": "gemini", "GEMINI_MODEL": "gemini-3.6-flash",
    "GEMINI_FALLBACK_MODELS": "gemini-3.5-flash-lite, gemini-2.5-flash",
    "CLAUDE_MODEL": "claude-sonnet-5-5", "CLAUDE_AUTH": "key",
    "OPENAI_BASE_URL": "https://openrouter.ai/api/v1", "OPENAI_MODEL": "", "OPENAI_FALLBACK_MODELS": "",
    "OLLAMA_URL": "http://localhost:11434", "OLLAMA_MODEL": "qwen2.5:7b", "OLLAMA_VISION_MODEL": "qwen2.5vl:7b",
}
PROVIDERS = {
    "gemini": {"label": "Gemini (Google, ada kuota gratis)", "model": "GEMINI_MODEL", "key": "GEMINI_API_KEY",
               "key_url": "https://aistudio.google.com/apikey", "fallback": "GEMINI_FALLBACK_MODELS"},
    "openai": {"label": "OpenAI-compatible (OpenRouter, Groq, DeepSeek, dll.)", "model": "OPENAI_MODEL",
               "key": "OPENAI_API_KEY", "key_url": None, "fallback": "OPENAI_FALLBACK_MODELS"},
    "claude": {"label": "Claude (Anthropic, berbayar)", "model": "CLAUDE_MODEL", "key": "ANTHROPIC_API_KEY",
               "key_url": "https://platform.claude.com/settings/keys", "fallback": None},
    "ollama": {"label": "Ollama (lokal, gratis)", "model": "OLLAMA_MODEL", "key": None, "key_url": None,
               "fallback": None},
}
# Alamat API OpenAI-compatible yang umum dipakai; bisa juga ketik alamat sendiri
OPENAI_PRESETS = [
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("Groq", "https://api.groq.com/openai/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("Together AI", "https://api.together.xyz/v1"),
    ("OpenAI", "https://api.openai.com/v1"),
    ("Ollama /v1 (lokal)", "http://localhost:11434/v1"),
    ("LM Studio (lokal)", "http://localhost:1234/v1"),
]
SETUP_HINT = "Atur di ⚙️ Pengaturan > 🤖 Model AI."
CLAUDE_LOGIN_HINT = ("Di VPS jalankan: ant auth login --no-browser (sebagai user yang menjalankan bot), "
                     "lalu tekan 🔄 Cek login di ⚙️ Pengaturan > 🤖 Model AI.")


def setting(name: str) -> str:
    return app_settings.get(name, DEFAULTS.get(name, "")).strip()


def provider() -> str:
    return setting("AI_PROVIDER").lower()


def current_model() -> str:
    info = PROVIDERS.get(provider())
    return setting(info["model"]) if info else ""


def claude_uses_login() -> bool:
    return setting("CLAUDE_AUTH") == "login"


def _ask(system: str, prompt: str, max_tokens: int = 2000, image: tuple[bytes, str] | None = None) -> str:
    """image: (isi file, mime type), misalnya poster lowongan."""
    name = provider()
    if name == "gemini":
        text = _ask_gemini(system, prompt, image)
    elif name == "openai":
        text = _ask_openai(system, prompt, max_tokens, image)
    elif name == "ollama":
        text = _ask_ollama(system, prompt, image)
    elif name == "claude":
        text = _ask_claude(system, prompt, max_tokens, image)
    else:
        raise ValueError(f"Provider AI tidak dikenal: {name}. {SETUP_HINT}")
    # Beberapa model menulis proses berpikirnya di <think>...</think>, buang bagian itu
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


# ---------------------------------------------------------------- coba ulang & model cadangan
# Server AI kadang sibuk (5xx) atau putus. Coba ulang dengan jeda makin lama di model yang sama,
# lalu pindah ke model cadangan (kuota & bebannya terpisah dari model utama).
RETRY_STATUS = {500, 502, 503, 504}
RETRY_DELAYS = (2, 5, 10)  # detik sebelum percobaan ke-2, ke-3, ke-4


class _ModelUnavailable(Exception):
    """Model ini sedang tidak bisa dipakai (sibuk / kuota habis / tidak ada). Coba model lain."""

    def __init__(self, reason: str, status: int):
        super().__init__(reason)
        self.status = status


def _json_chunks(resp: requests.Response) -> list[dict]:
    """Isi jawaban server sebagai daftar objek JSON.

    Biasanya satu objek, tapi sebagian server/proxy menjawab dalam bentuk streaming walau tidak diminta:
    SSE ("data: {...}"), NDJSON (satu JSON per baris), array JSON, atau beberapa objek yang menempel.
    """
    try:
        data = resp.json()
        return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else [data]
    except ValueError:
        pass
    lines = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]" or line.startswith((":", "event:", "id:", "retry:")):
            continue
        lines.append(line)
    text, decoder, chunks, i = "\n".join(lines), json.JSONDecoder(), [], 0
    while i < len(text):
        if text[i] in " \t\r\n,[]":
            i += 1
            continue
        try:
            value, i = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            break
        chunks += [c for c in value if isinstance(c, dict)] if isinstance(value, list) else [value]
    if not chunks:
        raise RuntimeError(f"Jawaban server tidak bisa dibaca (bukan JSON): {resp.text[:150]}")
    log.info("jawaban server berisi %d potongan JSON (streaming), digabung", len(chunks))
    return chunks


def _error_detail(resp: requests.Response) -> str:
    """Pesan error asli dari server (Gemini & OpenAI-compatible sama-sama memakai {"error": {"message": ...}})."""
    try:
        error = resp.json().get("error")
        message = error.get("message") if isinstance(error, dict) else error
    except (ValueError, AttributeError):
        message = None
    return " ".join(str(message or resp.text or "").split())[:200]


def _unavailable(resp: requests.Response) -> _ModelUnavailable:
    status, detail = resp.status_code, _error_detail(resp)
    if status == 404:
        reason = "model tidak ditemukan atau tidak tersedia untuk API key ini"
    elif status == 429 and "limit: 0" in resp.text:
        reason = "tidak termasuk kuota gratis API key ini (limit 0), perlu akun berbayar"
    elif status == 429:
        reason = "kuota / batas pemakaian habis untuk sementara"
    else:
        reason = "server sedang sibuk"
    return _ModelUnavailable(f"{reason} (HTTP {status}){': ' + detail if detail else ''}", status)


def _models_with_fallback(name: str) -> list[str]:
    info = PROVIDERS[name]
    fallbacks = setting(info["fallback"]).split(",") if info["fallback"] else []
    return list(dict.fromkeys(m for m in [setting(info["model"])] + [f.strip() for f in fallbacks] if m))


def _with_fallback(name: str, label: str, request) -> str:
    """Panggil request(model) untuk model utama, lalu tiap model cadangan sampai ada yang berhasil."""
    models = _models_with_fallback(name)
    if not models:
        raise RuntimeError(f"Model {label} belum dipilih. {SETUP_HINT}")
    failures = []
    for model in models:
        try:
            return request(model)
        except _ModelUnavailable as e:
            log.warning("%s %s gagal: %s", label, model, e)
            failures.append((model, e))
    statuses = {e.status for _, e in failures}
    if statuses <= {429}:
        headline = f"Semua model {label} yang dicoba sedang tidak bisa dipakai karena kuota."
    elif statuses <= {404}:
        headline = f"Model {label} yang dipilih tidak tersedia untuk API key kamu."
    elif statuses <= {0, 500, 502, 503, 504}:
        headline = (f"Server {label} sedang sibuk dan sudah dicoba beberapa kali. "
                    "Biasanya hanya sebentar, coba lagi beberapa menit lagi.")
    else:
        headline = f"Semua model {label} yang dicoba gagal."
    details = "\n".join(f"• {model}: {e}" for model, e in failures)
    raise RuntimeError(f"{headline}\n{details}\nLihat model yang tersedia lewat 🧠 Pilih model. {SETUP_HINT}")


def _post_with_retry(url: str, headers: dict, body: dict, retry_429: bool) -> requests.Response:
    """POST dengan coba ulang untuk error sementara. 404/429 dilempar sebagai _ModelUnavailable."""
    last = None
    for delay in (0,) + RETRY_DELAYS:
        time.sleep(delay)
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=180)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = _ModelUnavailable(f"koneksi gagal ({type(e).__name__})", 0)
            continue
        if resp.status_code == 429 and "limit: 0" in resp.text:
            raise _unavailable(resp)  # model di luar kuota gratis: menunggu tidak akan membantu
        if resp.status_code in RETRY_STATUS or (retry_429 and resp.status_code == 429):
            last = _unavailable(resp)
            continue
        if resp.status_code in (404, 429):
            # Tidak ada gunanya diulang di model yang sama; model cadangan punya kuota sendiri
            raise _unavailable(resp)
        return resp
    raise last


# ---------------------------------------------------------------- Gemini
def _ask_gemini(system: str, prompt: str, image: tuple[bytes, str] | None = None) -> str:
    parts = [{"text": prompt}]
    if image:
        parts.insert(0, {"inline_data": {"mime_type": image[1], "data": base64.b64encode(image[0]).decode()}})
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": parts}]}
    return _with_fallback("gemini", "Gemini", lambda model: _gemini_request(model, body))


def _gemini_request(model: str, body: dict) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": _api_key("gemini"), "Content-Type": "application/json"}
    resp = _post_with_retry(url, headers, body, retry_429=False)
    if not resp.ok:
        raise RuntimeError(f"Gemini error {resp.status_code}: {_error_detail(resp)}")
    texts, answered = [], False
    for chunk in _json_chunks(resp):
        for candidate in (chunk.get("candidates") or [])[:1]:
            answered = True
            parts = (candidate.get("content") or {}).get("parts") or []
            texts += [p.get("text", "") for p in parts if not p.get("thought")]
    if not answered:
        raise RuntimeError("Gemini tidak memberi jawaban (mungkin terkena filter). Coba lagi.")
    return "".join(texts)


# ---------------------------------------------------------------- OpenAI-compatible
def _openai_headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:  # server lokal (Ollama, LM Studio) biasanya tidak butuh key
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _ask_openai(system: str, prompt: str, max_tokens: int, image: tuple[bytes, str] | None = None) -> str:
    content = prompt
    if image:
        data_url = f"data:{image[1]};base64,{base64.b64encode(image[0]).decode()}"
        content = [{"type": "image_url", "image_url": {"url": data_url}}, {"type": "text", "text": prompt}]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    return _with_fallback("openai", "AI", lambda model: _openai_request(model, messages, max_tokens))


def _openai_request(model: str, messages: list, max_tokens: int) -> str:
    base = setting("OPENAI_BASE_URL").rstrip("/")
    resp = _post_with_retry(f"{base}/chat/completions", _openai_headers(setting("OPENAI_API_KEY")),
                            {"model": model, "messages": messages, "max_tokens": max_tokens}, retry_429=True)
    if resp.status_code in (401, 403):
        raise RuntimeError(f"API key ditolak oleh {base}. {SETUP_HINT}")
    if not resp.ok:
        raise RuntimeError(f"AI error {resp.status_code} dari {base}: {_error_detail(resp)}")
    texts, answered = [], False
    for chunk in _json_chunks(resp):
        for choice in (chunk.get("choices") or [])[:1]:
            answered = True
            # Jawaban biasa ada di "message", jawaban streaming di "delta"
            content = (choice.get("message") or choice.get("delta") or {}).get("content") or ""
            if isinstance(content, list):  # sebagian server mengembalikan potongan-potongan teks
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            texts.append(content)
    if not answered:
        raise RuntimeError("AI tidak memberi jawaban. Coba lagi atau pilih model lain.")
    return "".join(texts)


# ---------------------------------------------------------------- Ollama
def _ask_ollama(system: str, prompt: str, image: tuple[bytes, str] | None = None) -> str:
    base = setting("OLLAMA_URL").rstrip("/")
    user_msg = {"role": "user", "content": prompt}
    model = setting("OLLAMA_MODEL")
    if image:
        # Model teks biasa tidak bisa membaca gambar, pakai model vision
        model = setting("OLLAMA_VISION_MODEL")
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
    return "".join((chunk.get("message") or {}).get("content") or "" for chunk in _json_chunks(resp))


# ---------------------------------------------------------------- Claude
# Dua cara masuk: API key, atau login akun Claude Console lewat `ant auth login` di VPS.
# Saat login, SDK membaca profil login sendiri (Anthropic() tanpa api_key), asalkan
# ANTHROPIC_API_KEY tidak ada di environment, karena key (bahkan yang kosong) selalu menang.
CLAUDE_MIN_TOKENS = 16000  # model Claude terbaru berpikir dulu; max_tokens kecil bisa habis sebelum menjawab
_claude_clients = {}  # per API key / mode login, supaya ganti pengaturan dari bot langsung berlaku


def _claude_client(api_key: str | None):
    """api_key None = pakai login (ant auth login)."""
    try:
        import anthropic  # hanya di-import kalau memang pakai Claude
    except ImportError as e:
        raise RuntimeError("Library anthropic belum ter-install. Di VPS jalankan: "
                           ".venv/bin/pip install -U anthropic, lalu ./bot.sh restart") from e
    if api_key is None:
        env_key = os.environ.get("ANTHROPIC_API_KEY")
        if env_key == "":
            os.environ.pop("ANTHROPIC_API_KEY")  # baris kosong di .env akan menimpa login
        elif env_key:
            raise RuntimeError("ANTHROPIC_API_KEY di file .env menimpa login Claude. Hapus baris itu dari .env, "
                               "lalu ./bot.sh restart.")
    cache_key = api_key or "__login__"
    if cache_key not in _claude_clients:
        try:
            _claude_clients[cache_key] = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        except anthropic.AnthropicError as e:
            raise RuntimeError(f"Login Claude belum ditemukan. {CLAUDE_LOGIN_HINT}") from e
    return _claude_clients[cache_key]


def _claude_call(fn, api_key: str | None):
    """Jalankan fn(client) dan ubah error Anthropic menjadi pesan yang jelas."""
    import anthropic
    login = api_key is None
    try:
        return fn(_claude_client(api_key))
    except anthropic.AuthenticationError as e:
        _claude_clients.pop(api_key or "__login__", None)
        if login:
            raise RuntimeError(f"Login Claude tidak valid atau sudah kedaluwarsa. {CLAUDE_LOGIN_HINT}") from e
        raise RuntimeError("API key Claude tidak valid.") from e
    except anthropic.NotFoundError as e:
        raise RuntimeError(f"Model Claude tidak ditemukan. Pilih model lain: {SETUP_HINT}") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Claude error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Server Claude tidak bisa dihubungi. Coba lagi sebentar lagi.") from e
    except anthropic.AnthropicError as e:  # mis. kredensial tidak ditemukan saat request
        if login:
            raise RuntimeError(f"Login Claude belum ditemukan. {CLAUDE_LOGIN_HINT}") from e
        raise RuntimeError(f"Claude error: {e}") from e


def _claude_credentials() -> str | None:
    return None if claude_uses_login() else _api_key("claude")


def _ask_claude(system: str, prompt: str, max_tokens: int, image: tuple[bytes, str] | None = None) -> str:
    content = [{"type": "text", "text": prompt}]
    if image:
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": image[1], "data": base64.b64encode(image[0]).decode()}})
    resp = _claude_call(lambda client: client.messages.create(
        model=setting("CLAUDE_MODEL"),
        max_tokens=max(max_tokens, CLAUDE_MIN_TOKENS),
        system=system,
        messages=[{"role": "user", "content": content}],
    ), _claude_credentials())
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude menolak memproses permintaan ini. Coba lagi atau pilih model lain.")
    text = "".join(b.text for b in resp.content if b.type == "text")
    if not text and resp.stop_reason == "max_tokens":
        raise RuntimeError("Jawaban Claude terpotong sebelum selesai. Coba lagi.")
    return text


def _api_key(name: str) -> str:
    key = setting(PROVIDERS[name]["key"])
    if not key:
        raise RuntimeError(f"API key {name.capitalize()} belum diisi. {SETUP_HINT}")
    return key


# ---------------------------------------------------------------- pengaturan AI dari bot
# Model Gemini yang bukan untuk menjawab teks (suara, gambar, transkripsi, robotik, dll.)
NON_TEXT_MODELS = ("embedding", "tts", "image", "audio", "live", "native", "transcribe", "translate", "robotics",
                   "computer-use", "banana")


class NoModelList(RuntimeError):
    """Server tidak menyediakan daftar model (bukan berarti key salah)."""


def list_models(name: str, api_key: str | None = None) -> list[str]:
    """Daftar model yang tersedia langsung dari provider. Sekaligus memeriksa API key / login."""
    if name == "gemini":
        resp = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                            headers={"x-goog-api-key": api_key or _api_key("gemini")},
                            params={"pageSize": 1000}, timeout=30)
        if resp.status_code in (400, 401, 403):
            raise RuntimeError("API key Gemini tidak valid.")
        if not resp.ok:
            raise RuntimeError(f"Gemini error {resp.status_code}: {_error_detail(resp)}")
        names = {m["name"].removeprefix("models/") for m in _json_chunks(resp)[0].get("models") or []
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])}
        return sorted((n for n in names if n.startswith("gemini") and not any(x in n for x in NON_TEXT_MODELS)),
                      reverse=True)
    if name == "openai":
        base = setting("OPENAI_BASE_URL").rstrip("/")
        key = setting("OPENAI_API_KEY") if api_key is None else api_key
        try:
            resp = requests.get(f"{base}/models", headers=_openai_headers(key), timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"{base} tidak bisa dihubungi.") from e
        if resp.status_code in (401, 403):
            raise RuntimeError(f"API key tidak valid untuk {base}.")
        if not resp.ok:
            raise NoModelList(f"{base} tidak menyediakan daftar model (HTTP {resp.status_code}). "
                              "Ketik nama model secara manual.")
        ids = {m.get("id") for m in _json_chunks(resp)[0].get("data") or [] if m.get("id")}
        # Model gratis (OpenRouter memberi akhiran ":free") ditaruh paling atas
        return sorted(ids, key=lambda m: (not m.endswith(":free"), m))
    if name == "claude":
        credentials = api_key if api_key is not None else _claude_credentials()
        return _claude_call(lambda client: [m.id for m in client.models.list(limit=100)], credentials)
    if name == "ollama":
        base = setting("OLLAMA_URL").rstrip("/")
        try:
            resp = requests.get(f"{base}/api/tags", timeout=10)
        except requests.RequestException as e:
            raise RuntimeError(f"Ollama tidak bisa dihubungi di {base}. Pastikan Ollama jalan.") from e
        return sorted(m["name"] for m in _json_chunks(resp)[0].get("models") or [])
    raise ValueError(f"Provider tidak dikenal: {name}")


def ping() -> str:
    """Tes singkat: AI dengan pengaturan sekarang bisa menjawab atau tidak."""
    return _ask("Kamu sedang dites koneksinya.", "Balas hanya dengan kata: OK", max_tokens=20)


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
def tailor_cv(profile: dict, job_text: str, lang: str | None, facts: list[dict] | None = None,
              ask_missing: bool = True, skills: list[dict] | None = None) -> dict:
    """Sesuaikan CV dengan lowongan memakai metode PAR (Problem -> Action -> Result).

    facts: hasil nyata yang sudah dikonfirmasi kandidat, [{"poin": <poin asli>, "hasil": <jawaban>}].
    ask_missing: minta daftar poin penting yang belum punya hasil (perlu_hasil) untuk ditanyakan ke kandidat.
    skills: skill/pengalaman yang belum tertulis di CV tapi dikonfirmasi kandidat, [{"skill", "cerita"}].
    """
    language = "Bahasa Inggris" if lang == "en" else "Bahasa Indonesia"
    system = ("Kamu adalah penulis CV profesional yang SANGAT JUJUR. Kamu hanya boleh memakai "
              "fakta yang ada di CV asli dan hasil yang dikonfirmasi kandidat. " + JSON_ONLY)
    facts_text = "\n".join(f'- Poin: "{f["poin"]}" -> Hasil: {f["hasil"]}' for f in facts or []) or "(belum ada)"
    skills_text = "\n".join(f'- {k["skill"]}: {k["cerita"]}' for k in skills or []) or "(belum ada)"
    missing_rule = (
        "\n- Kalau sebuah poin penting untuk lowongan ini tidak punya hasil di CV asli maupun di HASIL DARI "
        "KANDIDAT,\n  tulis poin itu sebagai Problem + Action saja (JANGAN mengarang hasil), lalu masukkan ke "
        "perlu_hasil:\n  salin poin aslinya persis, dan tulis satu pertanyaan singkat bahasa Indonesia untuk "
        "kandidat tentang\n  hasil/dampaknya (contoh: \"Setelah sistem ini dipakai, apa yang berubah? Ada angka "
        "waktu, jumlah pengguna,\n  atau penghematan?\"). Maksimal 5 poin, pilih yang paling relevan dengan "
        "lowongan." if ask_missing else "")
    prompt = f"""Sesuaikan CV kandidat untuk lowongan di bawah supaya lebih relevan dan ramah ATS.

METODE PAR untuk setiap poin pengalaman dan proyek, ditulis sebagai satu kalimat ringkas:
- Problem: masalah atau kebutuhan yang dihadapi (singkat, sebagai konteks)
- Action: apa yang kandidat lakukan, diawali kata kerja aktif, pakai istilah dari lowongan
- Result: hasil atau dampaknya, sebisa mungkin terukur
- Hasil HANYA boleh diambil dari CV asli atau dari HASIL DARI KANDIDAT di bawah. JANGAN mengarang angka,
  persentase, atau dampak.{missing_rule}

BOLEH:
- menulis ulang ringkasan khusus untuk posisi ini
- mengurutkan ulang skill, pengalaman, dan poin supaya yang paling relevan ada di atas
- menulis ulang poin dengan metode PAR, SELAMA faktanya sama dengan poin asli + hasil dari kandidat
- menghapus poin yang tidak relevan (setiap pengalaman tetap minimal 1 poin)
- menerjemahkan jabatan dan poin ke {language}

DILARANG:
- menambah skill, tools, sertifikasi, pengalaman, proyek, angka, atau pencapaian yang tidak ada di CV asli
  maupun di HASIL DARI KANDIDAT atau SKILL TAMBAHAN DARI KANDIDAT
- mengubah nama perusahaan, periode, atau pendidikan
- mengubah atau menerjemahkan nama skill (salin persis dari CV asli atau SKILL TAMBAHAN DARI KANDIDAT)

SKILL TAMBAHAN DARI KANDIDAT boleh dimasukkan ke daftar skill (tulis nama skill persis), dan ceritanya boleh
dipakai di ringkasan atau sebagai poin di pengalaman/proyek yang sesuai, tanpa melebih-lebihkan isinya.

Tulis isi CV dalam {language}.

Balas JSON:
{{"profile": <objek dengan skema yang sama persis dengan profil asli>,
"perubahan": [3-6 poin singkat bahasa Indonesia tentang apa yang kamu ubah],
"saran_belajar": [skill penting dari lowongan yang BELUM dimiliki kandidat],
"perlu_hasil": [{{"poin_asli": "...", "pertanyaan": "..."}}]}}

PROFIL ASLI:
{json.dumps(profile, ensure_ascii=False)}

HASIL DARI KANDIDAT (sudah dikonfirmasi kandidat, boleh dipakai sebagai Result):
{facts_text}

SKILL TAMBAHAN DARI KANDIDAT (belum tertulis di CV, sudah dikonfirmasi kandidat):
{skills_text}

LOWONGAN:
{job_text[:7000]}"""
    result = _parse_json(_ask(system, prompt, max_tokens=6000))
    if not ask_missing:
        result["perlu_hasil"] = []
    return result


def improve_cv_par(profile: dict, facts: list[dict] | None = None, ask_missing: bool = True) -> dict:
    """Tulis ulang semua poin pengalaman & proyek di CV asli dengan metode PAR (CV umum, bukan per lowongan).

    Hanya poin yang dikembalikan; posisi, perusahaan, periode, dan nama proyek tidak disentuh.
    Hasil: {"pengalaman": [[poin...], ...], "proyek": [[...]], "perubahan": [...], "perlu_hasil": [...]}
    """
    def outline(sec, title):
        return [{"no": i + 1, "judul": title(e), "poin": e.get("poin") or []}
                for i, e in enumerate(profile.get(sec) or []) if isinstance(e, dict)]

    sections = {
        "pengalaman": outline("pengalaman", lambda e: f"{e.get('posisi')} - {e.get('perusahaan')} ({e.get('periode')})"),
        "proyek": outline("proyek", lambda e: e.get("nama")),
    }
    facts_text = "\n".join(f'- Poin: "{f["poin"]}" -> Hasil: {f["hasil"]}' for f in facts or []) or "(belum ada)"
    missing_rule = (
        "\n- Kalau poin penting tidak punya hasil di CV maupun di HASIL DARI KANDIDAT, tulis sebagai Problem + "
        "Action saja,\n  lalu masukkan ke perlu_hasil: salin poin aslinya persis dan tulis satu pertanyaan singkat "
        "bahasa Indonesia\n  tentang hasil/dampaknya. Maksimal 5 poin, pilih yang paling berdampak untuk karier "
        "kandidat." if ask_missing else "")
    system = ("Kamu adalah penulis CV profesional yang SANGAT JUJUR. Kamu hanya boleh memakai fakta yang ada di "
              "CV dan hasil yang dikonfirmasi kandidat. " + JSON_ONLY)
    prompt = f"""Tulis ulang SEMUA poin pengalaman dan proyek kandidat di bawah dengan metode PAR, untuk CV umum
(tidak untuk lowongan tertentu).

METODE PAR, satu kalimat ringkas per poin:
- Problem: masalah atau kebutuhan (singkat, sebagai konteks)
- Action: apa yang kandidat lakukan, diawali kata kerja aktif
- Result: hasil atau dampaknya, sebisa mungkin terukur
- Hasil HANYA dari poin asli atau HASIL DARI KANDIDAT. JANGAN mengarang angka, persentase, atau dampak.{missing_rule}

Aturan:
- Pertahankan jumlah dan urutan entri. Untuk tiap entri, pertahankan jumlah dan urutan poin (satu poin baru
  untuk setiap poin asli). Jangan menggabungkan, memecah, atau menghapus poin.
- Jangan menambah skill, tools, atau pencapaian yang tidak disebut.
- Pakai bahasa yang sama dengan poin aslinya.
- Poin yang sudah bagus boleh dibiarkan atau hanya dirapikan sedikit.

Balas JSON:
{{"pengalaman": [[poin baru untuk entri no 1], [poin baru untuk entri no 2], ...],
"proyek": [[...], ...],
"perubahan": [2-5 poin singkat bahasa Indonesia tentang apa yang kamu perbaiki],
"perlu_hasil": [{{"poin_asli": "...", "pertanyaan": "..."}}]}}

CV:
{json.dumps(sections, ensure_ascii=False)}

HASIL DARI KANDIDAT (sudah dikonfirmasi kandidat, boleh dipakai sebagai Result):
{facts_text}"""
    result = _parse_json(_ask(system, prompt, max_tokens=6000))
    if not ask_missing:
        result["perlu_hasil"] = []
    return result


def _norm(value) -> str:
    return str(value or "").strip().lower()


def guard_profile(original: dict, tailored: dict, extra_skills: list[str] | None = None) -> dict:
    """Pengaman tambahan: buang apa pun yang tidak ada di CV asli, kembalikan data faktual.

    extra_skills: skill yang belum tertulis di CV tapi sudah dikonfirmasi kandidat (boleh dipakai)."""
    result = dict(tailored)

    # Data pribadi, pendidikan, sertifikasi, bahasa selalu dari CV asli
    for key in ("nama", "email", "telepon", "lokasi", "tautan", "pendidikan", "sertifikasi", "bahasa"):
        result[key] = original.get(key)

    # Skill: hanya yang memang ada di CV asli atau dikonfirmasi kandidat
    orig_skills = {_norm(s) for s in (original.get("skill") or []) + list(extra_skills or [])}
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
    "formal": "Formal, sopan, dan profesional. 120-180 kata.",
    "santai": "Profesional tapi hangat dan luwes, tidak kaku, tetap sopan. 90-140 kata.",
    "singkat": "Singkat dan langsung ke inti, 2-3 kalimat isi. 50-80 kata.",
}

# Contoh struktur email lamaran formal (dari pengguna). Hanya acuan gaya, isinya tidak boleh disalin.
EMAIL_EXAMPLE = """Yth. HRD Manager,

Saya berharap email ini ditemukan dalam keadaan baik.

Perkenalkan nama saya <Nama> dan saya ingin menyampaikan minat saya untuk melamar posisi <Posisi> di perusahaan <Perusahaan>, sebagaimana diinformasikan di <sumber lowongan>. Dengan latar belakang saya di bidang <bidang> serta pengalaman dalam <2-4 keahlian paling relevan>, saya yakin dapat memberikan kontribusi yang berarti bagi tim.

Saya telah melampirkan CV dan portofolio saya untuk pertimbangan lebih lanjut. Saya sangat berharap dapat membawa pengalaman dan keahlian saya ke <Perusahaan> dan akan sangat senang untuk mendiskusikan bagaimana saya dapat berkontribusi bagi tim.

Terima kasih atas perhatian Anda terhadap lamaran saya. Saya sangat menantikan kesempatan untuk bisa bekerja sama dan siap untuk melakukan wawancara kapan saja sesuai kenyamanan Anda.

Hormat saya,

<Nama>
<Nomor telepon>
<Tautan portofolio / LinkedIn>"""


def write_cover_letter(profile: dict, job: dict, job_text: str, gaya: str = "formal") -> str:
    """Isi email lamaran. CV sudah dilampirkan, jadi isinya singkat dan tidak mengulang isi CV."""
    lang = "Bahasa Indonesia" if job.get("bahasa_lowongan") != "en" else "English"
    system = (
        "Kamu menulis isi email lamaran kerja yang wajar, sopan, dan jujur, seperti yang biasa dikirim pelamar. "
        "Tulis dalam teks biasa tanpa markdown, siap dikirim sebagai isi email."
    )
    source = job.get("sumber_info")
    prompt = f"""Tulis isi email lamaran dalam {lang} untuk posisi {job.get('posisi')} di {job.get('perusahaan')}.

Gaya: {GAYA.get(gaya, GAYA['formal'])}

PENTING: CV sudah dilampirkan di email, jadi JANGAN mengulang isi CV. Jangan merinci pengalaman kerja,
proyek, angka, pencapaian, atau daftar skill panjang. Cukup satu kalimat latar belakang yang menyebut
bidang kandidat dan 2-4 keahlian paling relevan dengan lowongan.

Struktur:
1. Salam pembuka (misalnya "Yth. HRD Manager," atau "Yth. Tim Rekrutmen {job.get('perusahaan') or ''},").
2. Perkenalan singkat: nama, posisi yang dilamar, perusahaan{', dan sumber lowongan: ' + source if source else ''}.
3. Satu kalimat latar belakang + keahlian yang relevan.
4. Menyebut CV terlampir{' dan portofolio' if profile.get('tautan') else ''} untuk pertimbangan.
5. Penutup: terima kasih dan siap untuk wawancara.
6. Tanda tangan: nama, nomor telepon, dan satu tautan portofolio/LinkedIn kalau ada di profil.

Contoh email formal yang disukai kandidat (acuan gaya dan struktur saja; JANGAN salin nama, perusahaan,
tautan, atau keahlian dari contoh ini, pakai data kandidat dan lowongan yang sebenarnya):
{EMAIL_EXAMPLE}

Aturan:
- Jangan pakai placeholder seperti <Nama> atau [Perusahaan]. Kalau sebuah info tidak diketahui
  (misalnya sumber lowongan), hilangkan saja bagian itu.
- Jangan mengarang pengalaman atau keahlian yang tidak ada di profil.{'' if profile.get('tautan') else '''
- Kandidat tidak punya tautan portofolio, jadi JANGAN menyebut portofolio (cukup CV terlampir).'''}

PROFIL KANDIDAT:
nama: {profile.get('nama')}
telepon: {profile.get('telepon')}
tautan: {json.dumps(profile.get('tautan') or [], ensure_ascii=False)}
ringkasan: {profile.get('ringkasan')}
skill: {', '.join(profile.get('skill') or [])}
posisi terakhir: {json.dumps([{'posisi': e.get('posisi'), 'perusahaan': e.get('perusahaan')} for e in (profile.get('pengalaman') or [])[:3] if isinstance(e, dict)], ensure_ascii=False)}

LOWONGAN:
{job_text[:4000]}"""
    return _ask(system, prompt, max_tokens=1200)


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
