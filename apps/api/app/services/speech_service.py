"""OpenAI text-to-speech for read-aloud replies (v3.7).

Replaces the renderer's robotic Web Speech voices for Ridian's receipt
read-aloud. The OPENAI_API_KEY — the same one Whisper voice input uses —
never leaves the backend; the renderer posts text to /tts/speak and gets
audio bytes back.

Voice and model are read from Settings ON EVERY CALL (load_settings is a
per-call file read), so changing the voice in Settings applies to the very
next reply — auditioning voices needs no restart.

Cost control is structural: the spoken text is hard-capped HERE at
MAX_TTS_CHARS (the renderer also trims to ~600 chars at a sentence
boundary), so no caller can send a novel to the per-character-billed API.
"""

from __future__ import annotations

import logging

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from .settings_service import get_effective_value, load_settings

log = logging.getLogger("ridian.speech")

ALLOWED_TTS_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
ALLOWED_TTS_MODELS = ("tts-1", "tts-1-hd")
DEFAULT_TTS_VOICE = "nova"
DEFAULT_TTS_MODEL = "tts-1"

# Server-side hard cap (characters). The renderer trims to ~600 at a
# sentence boundary; this bound is belt-and-suspenders for the paid API.
MAX_TTS_CHARS = 1200


class SpeechError(Exception):
    """``status`` maps to the HTTP status the API layer should return.
    ``detail`` is safe to surface — never contains the key."""

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


def effective_voice() -> str:
    """Settings voice, allowlisted — junk or blank resolves to the default."""
    v = (load_settings().get("openai_tts_voice") or "").strip().lower()
    return v if v in ALLOWED_TTS_VOICES else DEFAULT_TTS_VOICE


def effective_model() -> str:
    m = (load_settings().get("openai_tts_model") or "").strip().lower()
    return m if m in ALLOWED_TTS_MODELS else DEFAULT_TTS_MODEL


def synthesize_speech(text: str) -> bytes:
    """Text → MP3 bytes via OpenAI TTS. Raises SpeechError (400 config /
    502 upstream) — the caller decides how to degrade; the renderer falls
    back to browser speechSynthesis so text display is never affected."""
    clean = (text or "").strip()
    if not clean:
        raise SpeechError("text is required.", 400)
    key = get_effective_value("OPENAI_API_KEY")
    if not key:
        log.warning("speech.tts_failed reason=missing_key")
        raise SpeechError(
            "OPENAI_API_KEY is not set. Open Settings to add your key.", 400)
    clean = clean[:MAX_TTS_CHARS]
    voice = effective_voice()
    model = effective_model()
    # Precise failure mapping: every fallback the renderer takes must have a
    # NAMED reason in backend.log — a silent degrade taught us nothing.
    try:
        client = OpenAI(api_key=key)
        resp = client.audio.speech.create(model=model, voice=voice, input=clean)
        audio = resp.content
    except AuthenticationError as exc:
        log.warning("speech.tts_failed reason=invalid_key status=401")
        raise SpeechError(
            "OpenAI rejected the API key (401 Unauthorized) — check the key "
            "in Settings.", 502) from exc
    except RateLimitError as exc:
        log.warning("speech.tts_failed reason=rate_limit_or_quota status=429")
        raise SpeechError(
            "OpenAI rate limit or quota exceeded (429) — check your OpenAI "
            "usage/billing.", 502) from exc
    except APIConnectionError as exc:
        log.warning("speech.tts_failed reason=network type=%s", type(exc).__name__)
        raise SpeechError("Could not reach OpenAI (network error).", 502) from exc
    except APIStatusError as exc:
        status = getattr(exc, "status_code", "?")
        log.warning("speech.tts_failed reason=api_status status=%s", status)
        raise SpeechError(f"OpenAI TTS failed (HTTP {status}).", 502) from exc
    except Exception as exc:  # noqa: BLE001 — anything else, still named
        log.warning("speech.tts_failed reason=unexpected type=%s", type(exc).__name__)
        raise SpeechError(
            f"Text-to-speech failed ({type(exc).__name__}). "
            "Check your OpenAI key/quota.",
            502,
        ) from exc
    log.info("speech.tts chars=%d voice=%s model=%s bytes=%d",
             len(clean), voice, model, len(audio))
    return audio
