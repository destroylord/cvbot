import json

import pytest

import ai_helper
import app_settings

P = ai_helper._parse_json


class TestParseJson:
    def test_koma_berlebih_seperti_error_interview(self):
        broken = ('{"pertanyaan": [\n' + ",\n".join(f'{{"q": "Q{i}"}}' for i in range(10)) +
                  ',\n],\n"tanya_balik": ["a", "b",],\n}')
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)
        result = P(broken)
        assert len(result["pertanyaan"]) == 10 and result["tanya_balik"] == ["a", "b"]

    def test_teks_dan_code_fence_dibuang(self):
        assert P('Berikut hasilnya:\n```json\n{"a": 1}\n```\nSemoga membantu!') == {"a": 1}

    def test_jawaban_terpotong_ditutup(self):
        assert P('{"q": [{"tips": "Pakai proyek kas')["q"][0]["tips"] == "Pakai proyek kas"
        assert P('{"a": [1, 2, {"b": "x}y"}') == {"a": [1, 2, {"b": "x}y"}]}

    def test_koma_di_dalam_teks_tidak_disentuh(self):
        assert P('{"a": "teks dengan \\"kutip\\" dan , ]"}') == {"a": 'teks dengan "kutip" dan , ]'}
        assert P('{"a": "isi , ]", "b": [1, 2,],}') == {"a": "isi , ]", "b": [1, 2]}

    def test_tanpa_json(self):
        with pytest.raises(ValueError):
            P("maaf saya tidak bisa")


class TestAskJson:
    def test_minta_ulang_sekali_dengan_mode_json(self, monkeypatch):
        answers, calls = ["bukan json", '{"ok": true}'], []
        monkeypatch.setattr(ai_helper, "_ask", lambda s, p, m=2000, i=None, json_mode=False:
                            calls.append((p, json_mode)) or answers.pop(0))
        assert ai_helper._ask_json("s", "p") == {"ok": True}
        assert all(mode for _, mode in calls) and "bukan JSON valid" in calls[1][0]

    def test_dua_kali_rusak_pesan_jelas(self, monkeypatch):
        monkeypatch.setattr(ai_helper, "_ask", lambda *a, **k: "x")
        with pytest.raises(RuntimeError, match="format rusak dua kali"):
            ai_helper._ask_json("s", "p")


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self.ok, self._payload = status, status < 400, payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("bukan json")
        return self._payload


def gemini_ok(text="OK"):
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "mikir", "thought": True}, {"text": text}]}}]})


class TestGemini:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setattr(ai_helper.time, "sleep", lambda s: None)
        app_settings.set("AI_PROVIDER", "gemini")
        app_settings.set("GEMINI_API_KEY", "kunci")
        app_settings.set("GEMINI_MODEL", "gemini-utama")
        app_settings.set("GEMINI_FALLBACK_MODELS", "gemini-cadangan")
        self.calls, self.script = [], []

        def post(url, headers=None, json=None, timeout=None):
            self.calls.append({"model": url.split("/models/")[1].split(":")[0], "body": json, "key": headers["x-goog-api-key"]})
            return self.script.pop(0)
        monkeypatch.setattr(ai_helper.requests, "post", post)

    def test_sibuk_lalu_pulih(self):
        self.script[:] = [FakeResponse(503), gemini_ok("halo")]
        assert ai_helper.ping() == "halo"
        assert [c["model"] for c in self.calls] == ["gemini-utama"] * 2 and self.calls[0]["key"] == "kunci"

    def test_sibuk_terus_pindah_ke_cadangan(self):
        self.script[:] = [FakeResponse(503)] * 4 + [gemini_ok()]
        ai_helper.ping()
        assert self.calls[-1]["model"] == "gemini-cadangan"

    def test_limit_0_tidak_diulang_dan_alasannya_jelas(self):
        limit0 = FakeResponse(429, {"error": {"message": "Quota exceeded, limit: 0, model: x"}})
        self.script[:] = [limit0, FakeResponse(404, {"error": {"message": "model not found"}})]
        with pytest.raises(RuntimeError) as e:
            ai_helper.ping()
        assert "tidak termasuk kuota gratis" in str(e.value) and "model tidak ditemukan" in str(e.value)
        assert len(self.calls) == 2 and "{" not in str(e.value)

    def test_mode_json(self):
        self.script[:] = [gemini_ok('{"a": 1}'), gemini_ok("teks")]
        assert ai_helper._ask_json("s", "p") == {"a": 1}
        assert self.calls[0]["body"]["generationConfig"] == {"responseMimeType": "application/json"}
        ai_helper._ask("s", "p")
        assert "generationConfig" not in self.calls[1]["body"]

    def test_jawaban_streaming_digabung(self):
        chunks = json.dumps({"candidates": [{"content": {"parts": [{"text": "Halo "}]}}]}) + \
            json.dumps({"candidates": [{"content": {"parts": [{"text": "dunia"}]}}]})
        self.script[:] = [FakeResponse(200, None, chunks)]
        assert ai_helper.ping() == "Halo dunia"


class TestOpenAICompatible:
    def test_sse_dan_tanpa_key_untuk_server_lokal(self, monkeypatch):
        app_settings.set("AI_PROVIDER", "openai")
        app_settings.set("OPENAI_BASE_URL", "http://localhost:1234/v1/")
        app_settings.set("OPENAI_MODEL", "lokal")
        app_settings.set("OPENAI_API_KEY", "")
        seen = {}
        sse = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': t}}]})}\n\n" for t in ("Ha", "lo")) + "data: [DONE]\n"

        def post(url, headers=None, json=None, timeout=None):
            seen.update(url=url, auth=headers.get("Authorization"))
            return FakeResponse(200, None, sse)
        monkeypatch.setattr(ai_helper.requests, "post", post)
        assert ai_helper.ping() == "Halo"
        assert seen == {"url": "http://localhost:1234/v1/chat/completions", "auth": None}


def test_skill_karangan_dibuang_yang_dikonfirmasi_boleh():
    orig = {"skill": ["Laravel"], "pengalaman": []}
    result = ai_helper.guard_profile(orig, {"skill": ["Laravel", "Docker", "Kubernetes"]}, ["docker"])
    assert result["skill"] == ["Laravel", "Docker"]


def test_urutan_pengaturan_bot_env_default(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "dari-env")
    assert ai_helper.setting("GEMINI_MODEL") == "dari-env"
    app_settings.set("GEMINI_MODEL", "dari-bot")
    assert ai_helper.setting("GEMINI_MODEL") == "dari-bot"
    app_settings.set("GEMINI_FALLBACK_MODELS", "")
    assert ai_helper.setting("GEMINI_FALLBACK_MODELS") == ""
