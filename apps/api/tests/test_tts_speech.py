"""OpenAI TTS read-aloud (v3.7) — key server-side, capped, sanitized, live.

The /tts/speak endpoint replaces the renderer's robotic speechSynthesis.
Guarantees pinned here: the key never has to leave the backend (missing key
is an honest 400, upstream failure a 502 — never a 500), voice/model come
from Settings ON EVERY CALL (auditioning voices needs no restart), junk
settings sanitize to defaults, and the server hard-caps the billed
character count no matter what a caller sends.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import speech_service, settings_service

client = TestClient(app)


class _FakeSpeechAPI:
    def __init__(self, calls):
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)

        class _Resp:
            content = b"ID3fake-mp3-bytes"

        return _Resp()


def _patch(monkeypatch, *, key="sk-test", settings=None, boom=False):
    calls = []
    monkeypatch.setattr(speech_service, "get_effective_value",
                        lambda name: key if name == "OPENAI_API_KEY" else None)
    monkeypatch.setattr(speech_service, "load_settings",
                        lambda: dict(settings or {}))

    class _FakeClient:
        def __init__(self, api_key):
            calls.append({"api_key": api_key})
            if boom:
                raise RuntimeError("connection refused")
            self.audio = type("A", (), {"speech": _FakeSpeechAPI(calls)})()

    monkeypatch.setattr(speech_service, "OpenAI", _FakeClient)
    return calls


def _speech_calls(calls):
    return [c for c in calls if "input" in c]


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------

def test_speak_returns_audio_with_defaults(monkeypatch):
    calls = _patch(monkeypatch)
    res = client.post("/tts/speak", json={"text": "Hello from Ridian."})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/mpeg")
    assert res.content == b"ID3fake-mp3-bytes"
    call = _speech_calls(calls)[0]
    assert call["voice"] == "nova"          # default
    assert call["model"] == "tts-1"         # default (fast)
    assert call["input"] == "Hello from Ridian."
    assert calls[0]["api_key"] == "sk-test"  # the EXISTING Whisper key


def test_missing_key_is_400_not_500(monkeypatch):
    _patch(monkeypatch, key=None)
    res = client.post("/tts/speak", json={"text": "hi"})
    assert res.status_code == 400
    assert "Settings" in res.json()["detail"]


def test_upstream_failure_is_502_not_500(monkeypatch):
    _patch(monkeypatch, boom=True)
    res = client.post("/tts/speak", json={"text": "hi"})
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "key/quota" in detail
    assert "sk-" not in detail              # never leak the key


def test_empty_and_whitespace_text_400(monkeypatch):
    _patch(monkeypatch)
    assert client.post("/tts/speak", json={"text": ""}).status_code == 400
    assert client.post("/tts/speak", json={"text": "   "}).status_code == 400


# --------------------------------------------------------------------------
# Settings: sanitization + live (no-restart) voice changes
# --------------------------------------------------------------------------

def test_voice_and_model_from_settings(monkeypatch):
    calls = _patch(monkeypatch, settings={"openai_tts_voice": "Onyx",
                                          "openai_tts_model": "tts-1-hd"})
    client.post("/tts/speak", json={"text": "hi"})
    call = _speech_calls(calls)[0]
    assert call["voice"] == "onyx"          # case-normalized
    assert call["model"] == "tts-1-hd"


def test_junk_settings_sanitize_to_defaults(monkeypatch):
    calls = _patch(monkeypatch, settings={"openai_tts_voice": "hal9000",
                                          "openai_tts_model": "tts-9"})
    client.post("/tts/speak", json={"text": "hi"})
    call = _speech_calls(calls)[0]
    assert call["voice"] == "nova"
    assert call["model"] == "tts-1"


def test_voice_change_applies_next_call_without_restart(monkeypatch):
    """The audition guarantee: settings are read per CALL, so flipping the
    voice between two requests changes the second one — no restart."""
    live = {"openai_tts_voice": "nova"}
    calls = _patch(monkeypatch, settings=None)
    monkeypatch.setattr(speech_service, "load_settings", lambda: dict(live))
    client.post("/tts/speak", json={"text": "first"})
    live["openai_tts_voice"] = "onyx"       # the Settings save, mid-session
    client.post("/tts/speak", json={"text": "second"})
    voices = [c["voice"] for c in _speech_calls(calls)]
    assert voices == ["nova", "onyx"]


# --------------------------------------------------------------------------
# The billed-character cap
# --------------------------------------------------------------------------

def test_server_cap_bounds_billed_characters(monkeypatch):
    calls = _patch(monkeypatch)
    res = client.post("/tts/speak", json={"text": "x" * 50_000})
    assert res.status_code == 200
    call = _speech_calls(calls)[0]
    assert len(call["input"]) == speech_service.MAX_TTS_CHARS   # 1200, not 50k


def test_tts_settings_keys_are_settable():
    assert "openai_tts_voice" in settings_service.SETTABLE_KEYS
    assert "openai_tts_model" in settings_service.SETTABLE_KEYS
    # And they're not secrets — the renderer round-trips them freely.
    assert "openai_tts_voice" not in settings_service.SECRET_KEYS
