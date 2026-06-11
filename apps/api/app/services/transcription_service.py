"""Voice-input transcription (v1.7).

The renderer records mic audio with MediaRecorder (webm/opus), base64s it,
and POSTs to /operations/transcribe. We decode to a temp file and run it
through OpenAI Whisper. Cost is ~$0.006/minute — negligible — and it uses
the same OPENAI_API_KEY as everything else.

Why not the browser's webkitSpeechRecognition? It doesn't work in Electron
without a proprietary Google key baked at build time. Whisper does.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile

from openai import OpenAI

from .settings_service import apply_to_environment, get_effective_value

log = logging.getLogger("ridian.transcribe")

# ~60s of webm/opus is well under 1 MB; 15 MB is a generous ceiling that
# still prevents accidental uploads of huge files.
_MAX_BYTES = 15 * 1024 * 1024
_MIN_BYTES = 200  # anything smaller is a click, not speech

_SUFFIX_BY_MIME = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
}


class TranscriptionError(Exception):
    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


def transcribe_base64(audio_b64: str, mime: str = "audio/webm") -> str:
    """Decode base64 audio, run Whisper, return the transcript text."""
    apply_to_environment()
    key = get_effective_value("OPENAI_API_KEY")
    if not key:
        raise TranscriptionError("OPENAI_API_KEY is not set. Open Settings to add your key.", 400)
    if not audio_b64 or not audio_b64.strip():
        raise TranscriptionError("No audio received.", 400)

    try:
        raw = base64.b64decode(audio_b64)
    except Exception as exc:  # noqa: BLE001
        raise TranscriptionError("Audio payload is not valid base64.", 400) from exc

    if len(raw) < _MIN_BYTES:
        raise TranscriptionError("Recording too short — hold the mic button and speak.", 400)
    if len(raw) > _MAX_BYTES:
        raise TranscriptionError("Recording too large (15 MB cap — keep it under a minute).", 400)

    suffix = _SUFFIX_BY_MIME.get((mime or "").split(";")[0].strip().lower(), ".webm")
    fd, path = tempfile.mkstemp(prefix="ridian_voice_", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        client = OpenAI(api_key=key)
        with open(path, "rb") as f:
            result = client.audio.transcriptions.create(model="whisper-1", file=f)
        text = (getattr(result, "text", "") or "").strip()
        log.info("transcribe.ok bytes=%d chars=%d", len(raw), len(text))
        return text
    except TranscriptionError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("transcribe.failed type=%s", type(exc).__name__)
        raise TranscriptionError(
            f"Transcription failed ({type(exc).__name__}). Check your OpenAI key/quota.",
            502,
        ) from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
