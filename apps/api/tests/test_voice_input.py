"""Voice input / Whisper transcription path (v6.0 Phase 7).

The transcription service predates this phase; Phase 7 adds push-to-talk
and the command bar on top of it, so these pins cover the path both now
use: a real base64 → temp file → Whisper round trip (with the client
faked at the boundary), the honest refusals, and the guarantee that the
temp audio file is always deleted.
"""
import base64
import json
from pathlib import Path

import pytest

from app.services import transcription_service as ts


class _FakeTranscriptions:
    def __init__(self, sink, text="Invoice Sandy for the discovery engagement"):
        self.sink = sink
        self.text = text

    def create(self, model, file):
        self.sink["model"] = model
        self.sink["path"] = Path(file.name)
        self.sink["bytes"] = self.sink["path"].read_bytes()
        self.sink["existed_during_call"] = self.sink["path"].exists()
        return type("R", (), {"text": self.text})()


def _fake_openai(sink, text="Invoice Sandy for the discovery engagement"):
    class _Client:
        def __init__(self, api_key=None):
            sink["api_key"] = api_key
            self.audio = type("A", (), {"transcriptions": _FakeTranscriptions(sink, text)})()
    return _Client


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(ts, "apply_to_environment", lambda: None)
    monkeypatch.setattr(ts, "get_effective_value", lambda name: "sk-test-key")


def test_transcribes_audio_and_cleans_up(monkeypatch):
    sink = {}
    monkeypatch.setattr(ts, "OpenAI", _fake_openai(sink))
    audio = b"\x1aE\xdf\xa3fake-webm-opus-audio" + b"\x00" * 400
    text = ts.transcribe_base64(base64.b64encode(audio).decode(), "audio/webm")

    assert text == "Invoice Sandy for the discovery engagement"
    assert sink["model"] == "whisper-1"
    assert sink["api_key"] == "sk-test-key"
    assert sink["bytes"] == audio                 # the exact bytes we sent
    assert sink["path"].suffix == ".webm"         # mime → suffix mapping
    assert sink["existed_during_call"] is True
    assert not sink["path"].exists()              # temp file always removed


def test_mime_maps_to_the_right_suffix(monkeypatch):
    for mime, suffix in (("audio/mp4", ".m4a"), ("audio/wav", ".wav"),
                         ("audio/ogg", ".ogg"), ("audio/weird", ".webm")):
        sink = {}
        monkeypatch.setattr(ts, "OpenAI", _fake_openai(sink))
        ts.transcribe_base64(base64.b64encode(b"x" * 400).decode(), mime)
        assert sink["path"].suffix == suffix


def test_missing_key_refuses_before_any_call(monkeypatch):
    monkeypatch.setattr(ts, "get_effective_value", lambda name: "")

    def bomb(*a, **kw):
        raise AssertionError("no API call without a key")

    monkeypatch.setattr(ts, "OpenAI", bomb)
    with pytest.raises(ts.TranscriptionError) as exc:
        ts.transcribe_base64(base64.b64encode(b"x" * 400).decode())
    assert exc.value.status == 400 and "OPENAI_API_KEY" in exc.value.detail


@pytest.mark.parametrize("payload,fragment", [
    ("", "No audio"),
    ("not!valid!base64!", "not valid base64"),
])
def test_bad_payloads_refuse(payload, fragment, monkeypatch):
    monkeypatch.setattr(ts, "OpenAI", _fake_openai({}))
    with pytest.raises(ts.TranscriptionError) as exc:
        ts.transcribe_base64(payload)
    assert fragment in exc.value.detail


def test_click_length_recording_refuses(monkeypatch):
    """A stray click produces a few bytes — refuse rather than bill Whisper."""
    monkeypatch.setattr(ts, "OpenAI", _fake_openai({}))
    with pytest.raises(ts.TranscriptionError) as exc:
        ts.transcribe_base64(base64.b64encode(b"tiny").decode())
    assert "too short" in exc.value.detail.lower()


def test_oversized_recording_refuses(monkeypatch):
    monkeypatch.setattr(ts, "OpenAI", _fake_openai({}))
    big = base64.b64encode(b"\x00" * (ts._MAX_BYTES + 1)).decode()
    with pytest.raises(ts.TranscriptionError) as exc:
        ts.transcribe_base64(big)
    assert exc.value.status == 400 and "too large" in exc.value.detail.lower()


def test_upstream_failure_maps_to_502_and_still_cleans_up(monkeypatch):
    seen = {}

    class _Boom:
        def __init__(self, api_key=None):
            self.audio = type("A", (), {"transcriptions": self})()

        def create(self, model, file):
            seen["path"] = Path(file.name)
            raise RuntimeError("upstream exploded")

    monkeypatch.setattr(ts, "OpenAI", _Boom)
    with pytest.raises(ts.TranscriptionError) as exc:
        ts.transcribe_base64(base64.b64encode(b"x" * 400).decode())
    assert exc.value.status == 502
    assert "Check your OpenAI key/quota" in exc.value.detail
    assert not seen["path"].exists()              # cleanup on the failure path


# --------------------------------------------------------------------------
# Push-to-talk wiring (the renderer contract these paths serve)
# --------------------------------------------------------------------------

_RENDERER = Path(__file__).resolve().parents[3] / "desktop" / "renderer"


def test_composer_mic_is_push_to_talk_and_still_latches():
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    block = app_js.split("v6.0 Phase 7: PUSH-TO-TALK", 1)[1][:2000]
    for evt in ("pointerdown", "pointerup", "pointerleave", "pointercancel"):
        assert evt in block, evt
    assert "HOLD_MS" in block                     # hold vs click threshold
    assert "e.detail === 0" in block              # keyboard activation preserved


def test_command_bar_dictates_through_the_same_whisper_endpoint():
    bar_js = (_RENDERER / "commandbar.js").read_text(encoding="utf-8")
    assert "/operations/transcribe" in bar_js
    assert "pointerdown" in bar_js and "pointerup" in bar_js
    # Enter runs the command; Escape closes the bar.
    assert "'Enter'" in bar_js and "'Escape'" in bar_js
    assert "ridianBar.submit" in bar_js
