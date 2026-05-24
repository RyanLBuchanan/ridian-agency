"""Social Media Production workflow.

A single OpenAI Agents SDK agent produces a four-section Markdown package
(Content Package / Script / Caption Package / Posting Checklist). We split
it on the `# <Header>` markers and persist each section to its own file
under ``outputs/<timestamp>_<slug>/``.

Kept deliberately simple — one agent, one Runner.run, four files — so a
non-trivial brief still returns in roughly the same 30-90 seconds as the
business workflow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents import Agent, Runner, trace

from ..agents import default_model, load_prompt
from .artifact_service import create_run_folder, write_artifact
from .settings_service import apply_to_environment

# Section markers the agent emits. Order matters: we save artifact files
# in this order.
SECTION_HEADERS = (
    "Content Package",
    "Script",
    "Caption Package",
    "Posting Checklist",
)

# Map section header -> on-disk filename.
SECTION_FILENAMES = {
    "Content Package": "social_content_package.md",
    "Script": "script.md",
    "Caption Package": "caption_package.md",
    "Posting Checklist": "posting_checklist.md",
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


@dataclass
class SocialMediaResult:
    artifact_folder: Path
    content_package: str
    script: str
    caption_package: str
    posting_checklist: str


def _build_agent() -> Agent:
    return Agent(
        name="Social Media Agent",
        instructions=load_prompt("social_media_prompt.txt"),
        model=default_model(),
    )


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


async def run_social_media_workflow(payload: SocialMediaInput) -> SocialMediaResult:
    # Pick up any settings changes made since startup (key/model swap, etc.)
    apply_to_environment()

    agent = _build_agent()
    # Be defensive about model — agent above already reads default_model(),
    # but reassign in case settings changed between _build_agent() and now.
    agent.model = default_model()

    folder = create_run_folder(_slug_for_run(payload))
    formatted_input = _format_input(payload)

    with trace("ridian-agency.social-media"):
        result = await Runner.run(agent, input=formatted_input)

    raw = (result.final_output or "").strip()
    sections = _split_sections(raw)

    content_package = sections.get("Content Package", "")
    script = sections.get("Script", "")
    caption_package = sections.get("Caption Package", "")
    posting_checklist = sections.get("Posting Checklist", "")

    # Fallback: if the agent didn't use our headings at all, dump the whole
    # output into Content Package rather than losing it.
    if not any([content_package, script, caption_package, posting_checklist]):
        content_package = raw

    write_artifact(folder, SECTION_FILENAMES["Content Package"], content_package or "(empty)")
    write_artifact(folder, SECTION_FILENAMES["Script"], script or "(empty)")
    write_artifact(folder, SECTION_FILENAMES["Caption Package"], caption_package or "(empty)")
    write_artifact(folder, SECTION_FILENAMES["Posting Checklist"], posting_checklist or "(empty)")
    write_artifact(folder, "task.txt", formatted_input)

    return SocialMediaResult(
        artifact_folder=folder,
        content_package=content_package,
        script=script,
        caption_package=caption_package,
        posting_checklist=posting_checklist,
    )
