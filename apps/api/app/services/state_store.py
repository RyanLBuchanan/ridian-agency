"""Generic JSON state store for Ridian Command Center.

A thin wrapper over the pattern already used by ``settings_service.py``:
load/save named JSON files under ``apps/api/state/``. Writes are atomic
(temp file + rename) so a crash mid-write can't truncate state.

Local-only. Never returns secrets. Files in ``state/`` are git-ignored.

Each named state file holds either a list of objects (contacts, facts,
follow-ups, decisions) or a single dict (brand). Callers supply the
default so empty/missing files round-trip without surprises.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("ridian.state")

# apps/api/app/services/state_store.py -> apps/api/state/
STATE_DIR = Path(__file__).resolve().parent.parent.parent / "state"


def _path_for(name: str) -> Path:
    """Resolve a safe path inside STATE_DIR for the given state name.

    Prevents path traversal by rejecting names with separators or dots.
    Only bare slugs like ``contacts`` or ``follow_ups`` are accepted.
    """
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise ValueError(f"Invalid state name: {name!r}")
    return STATE_DIR / f"{name}.json"


def load(name: str, default: Any) -> Any:
    """Read a state file. Returns ``default`` if missing or unreadable."""
    path = _path_for(name)
    if not path.exists():
        return default
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return default
        return json.loads(text)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("state.load_failed name=%s type=%s", name, type(exc).__name__)
        return default


def save(name: str, data: Any) -> Any:
    """Write a state file atomically. Returns the data that was written."""
    path = _path_for(name)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=False) + "\n"
    # Atomic write: tmp file in same dir, then rename.
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{name}.", suffix=".tmp", dir=str(STATE_DIR)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, path)
    except OSError as exc:
        log.warning("state.save_failed name=%s type=%s", name, type(exc).__name__)
        # Clean up the temp file if rename failed.
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    log.info("state.saved name=%s", name)
    return data


def load_list(name: str) -> list[dict]:
    """Load a state file expected to contain a list of dicts."""
    data = load(name, default=[])
    if not isinstance(data, list):
        log.warning("state.bad_format expected_list name=%s", name)
        return []
    return [d for d in data if isinstance(d, dict)]


def load_dict(name: str, default: dict | None = None) -> dict:
    """Load a state file expected to contain a single dict."""
    data = load(name, default=default or {})
    if not isinstance(data, dict):
        log.warning("state.bad_format expected_dict name=%s", name)
        return default or {}
    return data
