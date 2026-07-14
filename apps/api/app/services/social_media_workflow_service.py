"""Social Media Production workflow.

A single OpenAI Agents SDK agent produces a five-section Markdown package
(Content Package / Script / Caption Package / Posting Checklist / Visual
Production). We split it on the `# <Header>` markers and persist each
section to its own file under ``outputs/<timestamp>_<slug>/``.

Kept deliberately simple — one agent, one call, five files — so a
non-trivial brief still returns in roughly the same 30-90 seconds as the
business workflow.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..agents import load_prompt
from .anthropic_runtime import run_text_agent
from .artifact_service import create_run_folder, write_artifact
from .settings_service import apply_to_environment

log = logging.getLogger("ridian.social")

_ALLOWED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Section markers the agent emits. Order matters: we save artifact files
# in this order.
SECTION_HEADERS = (
    "Content Package",
    "Script",
    "Caption Package",
    "Posting Checklist",
    "Visual Production",
)

# Map section header -> on-disk filename.
SECTION_FILENAMES = {
    "Content Package": "social_content_package.md",
    "Script": "script.md",
    "Caption Package": "caption_package.md",
    "Posting Checklist": "posting_checklist.md",
    "Visual Production": "visual_production.md",
}

_HEADER_RE = re.compile(r"^#\s+(.+?)\s*$")


@dataclass
class SocialMediaInput:
    channel: str
    starting_point: str
    content_format: str
    media_notes: str
    topic_notes: str
    goal: str
    output_depth: str
    image_data: Optional[str] = field(default=None, repr=False)


@dataclass
class SocialMediaResult:
    artifact_folder: Path
    content_package: str
    script: str
    caption_package: str
    posting_checklist: str
    visual_production: str


def _format_input(payload: SocialMediaInput) -> str:
    """Turn the structured request into the prompt the agent reads."""
    parts = [
        f"Channel: {payload.channel}",
        f"Starting point: {payload.starting_point}",
        f"Content format: {payload.content_format}",
        f"Goal: {payload.goal}",
        f"Output depth: {payload.output_depth}",
    ]
    if (payload.media_notes or "").strip():
        parts.append("\nMedia notes:\n" + payload.media_notes.strip())
    if (payload.topic_notes or "").strip():
        parts.append("\nTopic notes:\n" + payload.topic_notes.strip())
    return "\n".join(parts)


def _split_sections(md: str) -> dict[str, str]:
    """Split agent Markdown on `# <SECTION_HEADER>` lines.

    Headings that don't match one of SECTION_HEADERS are treated as body
    content of the current section (so nested `## Subheadings` are fine).
    """
    sections: dict[str, str] = {h: "" for h in SECTION_HEADERS}
    current: Optional[str] = None
    buf: list[str] = []

    lines = md.replace("\r\n", "\n").split("\n")
    for line in lines:
        m = _HEADER_RE.match(line.strip())
        matched_section = None
        if m:
            title = m.group(1).strip()
            for h in SECTION_HEADERS:
                if title.lower() == h.lower():
                    matched_section = h
                    break

        if matched_section is not None:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = matched_section
            buf = []
            continue

        if current is not None:
            buf.append(line)

    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _slug_for_run(payload: SocialMediaInput) -> str:
    """Pick a short, human-readable slug for the per-run folder."""
    seed = payload.topic_notes or payload.media_notes or payload.content_format or "social"
    return f"{payload.channel} - {seed[:50]}"


def _parse_data_uri(data_uri: str) -> tuple[str, bytes]:
    """Parse a ``data:<mime>;base64,<data>`` URI. Returns (mime, raw_bytes)."""
    if not data_uri.startswith("data:"):
        raise ValueError("Not a data URI")
    header, _, encoded = data_uri.partition(",")
    mime = header.split(";")[0].removeprefix("data:")
    raw = base64.b64decode(encoded)
    return mime, raw


def _ext_from_mime(mime: str) -> str:
    m = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    return m.get(mime.lower(), ".png")


def _save_thumbnail(folder: Path, data_uri: str) -> Optional[str]:
    """Validate, decode, and save the image to the run folder.

    Returns the saved filename or None on failure.
    """
    try:
        mime, raw = _parse_data_uri(data_uri)
    except Exception:
        log.warning("social.thumbnail.invalid_data_uri")
        return None

    ext = _ext_from_mime(mime)
    if ext not in _ALLOWED_IMAGE_EXTS:
        log.warning("social.thumbnail.disallowed_ext ext=%s", ext)
        return None
    if len(raw) > _MAX_IMAGE_BYTES:
        log.warning("social.thumbnail.too_large size=%d", len(raw))
        return None

    filename = f"input_thumbnail{ext}"
    path = folder / filename
    path.write_bytes(raw)
    log.info("social.thumbnail.saved path=%s size=%d", filename, len(raw))
    return filename


def _build_agent_input(text: str, image_data_uri: Optional[str]):
    """Build the user content — plain string or Anthropic multimodal blocks."""
    if not image_data_uri:
        return text

    # data:image/png;base64,<data> → Anthropic base64 image source block
    try:
        header, b64 = image_data_uri.split(",", 1)
        media_type = header.split(":", 1)[1].split(";", 1)[0] or "image/png"
    except (ValueError, IndexError):
        return text
    return [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
        {"type": "text", "text": text},
    ]


async def run_social_media_workflow(payload: SocialMediaInput) -> SocialMediaResult:
    # Pick up any settings changes made since startup (key/model swap, etc.)
    apply_to_environment()

    folder = create_run_folder(_slug_for_run(payload))
    formatted_input = _format_input(payload)

    # Save thumbnail to run folder and build multimodal input if present
    image_data_uri = None
    if payload.image_data:
        saved = _save_thumbnail(folder, payload.image_data)
        if saved:
            image_data_uri = payload.image_data
            formatted_input += "\n\nImage context: An image/thumbnail has been provided. Analyze it and use the visual elements to shape the content package."
        else:
            formatted_input += "\n\nNote: An image was provided but could not be processed. Proceed with text-only context."

    agent_input = _build_agent_input(formatted_input, image_data_uri)

    raw = (await run_text_agent(
        load_prompt("social_media_prompt.txt"), agent_input,
    )).strip()
    sections = _split_sections(raw)

    content_package = sections.get("Content Package", "")
    script = sections.get("Script", "")
    caption_package = sections.get("Caption Package", "")
    posting_checklist = sections.get("Posting Checklist", "")
    visual_production = sections.get("Visual Production", "")

    # Fallback: if the agent didn't use our headings at all, dump the whole
    # output into Content Package rather than losing it.
    if not any([content_package, script, caption_package, posting_checklist]):
        content_package = raw

    write_artifact(folder, SECTION_FILENAMES["Content Package"], content_package or "(empty)")
    write_artifact(folder, SECTION_FILENAMES["Script"], script or "(empty)")
    write_artifact(folder, SECTION_FILENAMES["Caption Package"], caption_package or "(empty)")
    write_artifact(folder, SECTION_FILENAMES["Posting Checklist"], posting_checklist or "(empty)")
    write_artifact(folder, SECTION_FILENAMES["Visual Production"], visual_production or "(empty)")
    write_artifact(folder, "task.txt", formatted_input)

    return SocialMediaResult(
        artifact_folder=folder,
        content_package=content_package,
        script=script,
        caption_package=caption_package,
        posting_checklist=posting_checklist,
        visual_production=visual_production,
    )
