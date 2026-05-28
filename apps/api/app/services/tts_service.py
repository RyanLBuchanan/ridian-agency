"""Text-to-speech for Operator audiobook artifacts.

Wraps the OpenAI TTS API (``client.audio.speech.create``). Produces real
MP3 bytes — no prompt-shaped output.

Two-voice strategy: we split a NotebookLM-style script on speaker labels
(``**Host A**:`` / ``**Host B**:``), synthesize each speaker's lines in
their assigned voice, and concatenate MP3 byte streams. MP3 frames are
self-contained, so byte-level concatenation produces a single playable
file in every common player.

If something fails (no key, model unavailable, network), the caller gets
``TTSError`` and the operation marks audio as unavailable rather than
faking it.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from openai import OpenAI

from .settings_service import apply_to_environment, get_effective_value

log = logging.getLogger("ridian.tts")

# OpenAI TTS hard limit per request.
_TTS_CHAR_LIMIT = 4000

DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE_A = "onyx"
DEFAULT_VOICE_B = "nova"

# Regex matches:  **Host A:** ...    or    **Host A**: ...    or    Host A: ...
# Anchored to line start. Captures speaker label and the rest of the line.
_SPEAKER_RE = re.compile(
    r"^\s*(?:\*\*)?\s*(Host\s*A|Host\s*B|Speaker\s*A|Speaker\s*B|A|B)\s*(?:\*\*)?\s*:\s*(.*)$",
    re.IGNORECASE,
)


class TTSError(Exception):
    """Raised when audio synthesis cannot complete. Detail is safe to surface."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


@dataclass
class AudioSegment:
    voice: str
    text: str


def _client() -> OpenAI:
    apply_to_environment()
    key = get_effective_value("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise TTSError("OPENAI_API_KEY is not set. Open Settings to add your key.")
    return OpenAI(api_key=key)


def _which_voice(label: str, voice_a: str, voice_b: str) -> str:
    """Pick the voice for a speaker label. Defaults to voice_a."""
    norm = re.sub(r"\s+", "", (label or "").lower())
    if norm.endswith("b"):
        return voice_b
    return voice_a


def _strip_md_stage_directions(text: str) -> str:
    """Drop bracketed stage directions / italic emphasis that shouldn't be spoken."""
    text = re.sub(r"\[(?:music|laughs?|pause|sfx)[^\]]*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(([^)]{0,40})\)", lambda m: m.group(1), text)  # keep short parentheticals as plain text
    text = re.sub(r"[*_`]+", "", text)  # strip bold/italic markers
    return text.strip()


def script_to_segments(script_md: str, voice_a: str = DEFAULT_VOICE_A, voice_b: str = DEFAULT_VOICE_B) -> list[AudioSegment]:
    """Parse a NotebookLM-style script into ordered audio segments.

    Lines without a speaker label inherit the previous speaker (so multi-paragraph
    speaker turns work). Lines before any speaker label are spoken in voice_a.
    Markdown chapter headers (``## Chapter…``) become a brief narrator line so
    the audiobook has audible breaks.
    """
    segments: list[AudioSegment] = []
    current_voice = voice_a
    buf: list[str] = []

    def flush():
        if not buf:
            return
        text = " ".join(s.strip() for s in buf if s.strip()).strip()
        if text:
            for chunk in _chunk_text(text, _TTS_CHAR_LIMIT):
                segments.append(AudioSegment(voice=current_voice, text=chunk))
        buf.clear()

    for raw_line in (script_md or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            buf.append("")
            continue

        # Chapter headers: spoken as a short narrator beat in voice_a.
        if line.lstrip().startswith("## "):
            flush()
            heading = line.lstrip("# ").strip()
            if heading:
                current_voice = voice_a
                buf.append(f"Chapter: {heading}.")
                flush()
            continue
        if line.lstrip().startswith("# "):
            # Top-level title — say it once at the very start.
            flush()
            title = line.lstrip("# ").strip()
            if title:
                current_voice = voice_a
                buf.append(title + ".")
                flush()
            continue

        m = _SPEAKER_RE.match(line)
        if m:
            flush()
            current_voice = _which_voice(m.group(1), voice_a, voice_b)
            remainder = _strip_md_stage_directions(m.group(2))
            if remainder:
                buf.append(remainder)
            continue

        # Continuation line — same speaker.
        cleaned = _strip_md_stage_directions(line)
        if cleaned:
            buf.append(cleaned)

    flush()
    return segments


def _chunk_text(text: str, limit: int) -> list[str]:
    """Split long speaker turns at sentence boundaries, then word boundaries."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Prefer the last sentence boundary inside the limit.
        slice_ = remaining[:limit]
        cut = max(slice_.rfind(". "), slice_.rfind("! "), slice_.rfind("? "))
        if cut < int(limit * 0.4):
            cut = slice_.rfind(" ")
        if cut < 0:
            cut = limit
        parts.append(remaining[:cut + 1].strip())
        remaining = remaining[cut + 1:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def synthesize_segment(client: OpenAI, voice: str, text: str, model: str = DEFAULT_MODEL) -> bytes:
    """Synthesize one speaker turn to MP3 bytes. Raises TTSError on failure."""
    try:
        resp = client.audio.speech.create(
            model=model,
            voice=voice,
            input=text,
            response_format="mp3",
        )
    except Exception as exc:
        raise TTSError(f"TTS request failed ({type(exc).__name__}): {exc}") from exc
    try:
        return resp.read()
    except Exception as exc:
        raise TTSError(f"TTS response read failed ({type(exc).__name__}): {exc}") from exc


def synthesize_audiobook(
    script_md: str,
    output_path: Path,
    *,
    voice_a: str = DEFAULT_VOICE_A,
    voice_b: str = DEFAULT_VOICE_B,
    model: str = DEFAULT_MODEL,
    progress_cb=None,
) -> dict:
    """Synthesize an audiobook MP3 from a NotebookLM-style script.

    ``progress_cb(done, total)`` is called after each segment so the operator
    timeline can show progress. Returns metadata: ``{path, segments,
    bytes, voices}``.
    """
    segments = script_to_segments(script_md, voice_a=voice_a, voice_b=voice_b)
    if not segments:
        raise TTSError("Script produced no speakable text after parsing.")

    client = _client()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(segments)
    written_bytes = 0
    # Open once, append each segment's MP3 bytes. MP3 frames are self-contained.
    with output_path.open("wb") as f:
        for i, seg in enumerate(segments, start=1):
            data = synthesize_segment(client, seg.voice, seg.text, model=model)
            f.write(data)
            written_bytes += len(data)
            if progress_cb:
                try:
                    progress_cb(i, total)
                except Exception:
                    pass  # never let progress callback break synthesis

    log.info(
        "tts.complete path=%s segments=%d bytes=%d voices=%s/%s",
        output_path, total, written_bytes, voice_a, voice_b,
    )
    return {
        "path": str(output_path),
        "segments": total,
        "bytes": written_bytes,
        "voices": [voice_a, voice_b],
    }


def estimate_runtime_seconds(script_md: str, wpm: int = 165) -> int:
    """Rough spoken-runtime estimate for a script. Used for plan messaging."""
    words = len(re.findall(r"\b\w+\b", script_md or ""))
    if words == 0:
        return 0
    return int(round(words / max(wpm, 1) * 60))
