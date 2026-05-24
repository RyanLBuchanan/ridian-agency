"""Recent-project listing and loading for the sidebar.

The desktop sidebar wants two things:

1. A list of recent runs (so the operator can see what's been done lately).
2. The ability to re-open one of those runs without re-calling the model —
   load the saved Markdown files back into the GUI.

This service handles both with the same security model the rest of the
backend uses:

- The folder must resolve inside the configured ``outputs/`` directory
  (no path traversal, no arbitrary paths, no outputs root).
- Only allowlisted filenames are read.
- The actual file *contents* are returned to the renderer, but we never
  read ``.env``, ``local_settings.json``, ``google_credentials.json``,
  ``google_token.json``, or anything outside the per-run folder.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .artifact_service import outputs_dir

log = logging.getLogger("ridian.projects")

# Mapping from on-disk filename to the JSON key the renderer expects.
# Matches the response shapes of /workflows/run and /workflows/social-media/run
# so the renderer can render either kind with its existing logic.
LOAD_FIELD_MAP: dict[str, str] = {
    # Business workflow
    "research_summary.md":       "research_summary",
    "business_document.md":      "business_document",
    "slide_outline.md":          "slide_outline",
    "draft_email.md":            "draft_email",
    # Social media production
    "social_content_package.md": "content_package",
    "script.md":                 "script",
    "caption_package.md":        "caption_package",
    "posting_checklist.md":      "posting_checklist",
}

_SOCIAL_MARKERS = (
    "social_content_package.md",
    "script.md",
    "caption_package.md",
    "posting_checklist.md",
)
_BUSINESS_MARKERS = (
    "research_summary.md",
    "business_document.md",
    "slide_outline.md",
    "draft_email.md",
)


class ProjectError(Exception):
    """Raised when a project lookup is invalid. Maps to an HTTP status."""

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Path validation (identical security model to export_service)
# ---------------------------------------------------------------------------


def _resolve_project_folder(folder_str: str) -> Path:
    if not folder_str or not isinstance(folder_str, str):
        raise ProjectError("artifact_folder is required.", status=400)
    try:
        candidate = Path(folder_str).resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise ProjectError(
            f"Invalid folder path ({type(exc).__name__}).", status=400
        ) from exc

    outputs = outputs_dir().resolve()
    try:
        candidate.relative_to(outputs)
    except ValueError as exc:
        raise ProjectError(
            "Folder is not inside the configured outputs directory.", status=400
        ) from exc
    if not candidate.exists():
        raise ProjectError("Artifact folder does not exist.", status=404)
    if not candidate.is_dir():
        raise ProjectError("Artifact path is not a directory.", status=400)
    if candidate.resolve() == outputs:
        raise ProjectError(
            "Refusing to operate on the outputs root. Provide a per-run folder.",
            status=400,
        )
    return candidate


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _read_channel_from_task(folder: Path) -> str:
    """Pull the ``Channel:`` line out of task.txt (case-insensitive)."""
    f = folder / "task.txt"
    if not f.is_file():
        return ""
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*Channel\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
    except OSError:
        return ""
    return ""


def _detect_workflow(folder: Path) -> str:
    """Detect 'social', 'business', or 'unknown' from file presence."""
    try:
        files = {p.name for p in folder.iterdir() if p.is_file()}
    except OSError:
        return "unknown"
    if any(m in files for m in _SOCIAL_MARKERS):
        return "social"
    if any(m in files for m in _BUSINESS_MARKERS):
        return "business"
    return "unknown"


def _project_meta(folder: Path) -> dict:
    workflow = _detect_workflow(folder)
    channel = _read_channel_from_task(folder) if workflow == "social" else ""
    mtime = folder.stat().st_mtime
    return {
        "artifact_folder": str(folder),
        "name": folder.name,
        "workflow": workflow,
        "channel": channel,
        "mtime_iso": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def list_recent_projects(limit: int = 30) -> list[dict]:
    """List recent run folders, newest first, capped at ``limit``."""
    outputs = outputs_dir()
    if not outputs.exists():
        return []

    items: list[dict] = []
    for sub in outputs.iterdir():
        if not sub.is_dir():
            continue
        if sub.name.startswith("."):
            continue
        try:
            items.append(_project_meta(sub))
        except (OSError, PermissionError) as exc:
            log.warning(
                "project.meta_failed name=%s type=%s", sub.name, type(exc).__name__
            )

    items.sort(key=lambda x: x.get("mtime_iso", ""), reverse=True)
    return items[:limit]


def load_project(folder_str: str) -> dict:
    """Read allowlisted Markdown files for a single run and return them.

    Returns a payload compatible with both workflow response shapes — the
    renderer reads whichever fields are present.
    """
    folder = _resolve_project_folder(folder_str)
    workflow = _detect_workflow(folder)
    channel = _read_channel_from_task(folder)

    out: dict[str, Optional[str]] = {
        "artifact_folder": str(folder),
        "name": folder.name,
        "workflow": workflow,
        "channel": channel,
        "task": "",
    }

    task_file = folder / "task.txt"
    if task_file.is_file():
        try:
            out["task"] = task_file.read_text(encoding="utf-8")
        except OSError:
            out["task"] = ""

    for filename, field in LOAD_FIELD_MAP.items():
        p = folder / filename
        if p.is_file():
            try:
                out[field] = p.read_text(encoding="utf-8")
            except OSError:
                out[field] = ""
        else:
            out[field] = ""

    return out
