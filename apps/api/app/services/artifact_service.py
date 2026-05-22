"""Filesystem-backed artifact storage.

Each workflow run gets its own timestamped folder under ``outputs/``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

# repo root = three levels up from this file:
# .../ridian-agency/apps/api/app/services/artifact_service.py
#                                ^app  ^api  ^apps  ^ridian-agency
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUTS_DIR = REPO_ROOT / "outputs"


def _outputs_dir() -> Path:
    override = os.getenv("OUTPUTS_DIR")
    return Path(override).resolve() if override else DEFAULT_OUTPUTS_DIR


def outputs_dir() -> Path:
    """Public accessor for callers outside this module."""
    return _outputs_dir()


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_len] or "task"


def create_run_folder(task: str) -> Path:
    outputs = _outputs_dir()
    outputs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = outputs / f"{stamp}_{_slugify(task)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_artifact(folder: Path, filename: str, content: str) -> Path:
    path = folder / filename
    path.write_text(content, encoding="utf-8")
    return path
