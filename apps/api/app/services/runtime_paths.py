"""Frozen-vs-dev path resolution (v4.2, self-contained backend).

THE contract (approved 2026-07-21): branch on ``sys.frozen`` only.
  - DEV (uvicorn from the venv): every path is EXACTLY what it was before
    this module existed — apps/api for writable state, source tree for
    static resources. Byte-identical behavior.
  - FROZEN (PyInstaller build): writable state lives in
    ``%APPDATA%\\Ridian Operator\\`` (settings, memory/state store, OAuth
    tokens, logs, outputs); static resources (prompts, static/) come from
    the bundle. Secrets are runtime config files in the data dir — NEVER
    frozen into the binary.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Ridian Operator"

# apps/api/app/services/runtime_paths.py -> apps/api (the historical base
# for every writable file in dev mode).
_API_DIR = Path(__file__).resolve().parent.parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    """Base for ALL writable state. Dev: apps/api (unchanged). Frozen:
    %APPDATA%/Ridian Operator (created on first use)."""
    if not is_frozen():
        return _API_DIR
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    d = Path(base) / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


# Everything writable a legacy (repo-based) install may hold.
_MIGRATABLE = ("local_settings.json", "google_credentials.json",
               "google_token.json", "quickbooks_token.json", ".env", "state")


def migrate_legacy_state(src: Path, dst: Path) -> list[str]:
    """Byte-copy writable state from a legacy layout (a repo's apps/api)
    into the data dir. shutil.copy2/copytree ONLY — files are never parsed,
    filtered, or rewritten, so memory provenance stamps
    (written_by/source_op) survive BYTE-IDENTICAL by construction (pinned
    by test_frozen_paths). Existing destination files are never overwritten.
    Returns the names copied."""
    import shutil
    copied: list[str] = []
    dst.mkdir(parents=True, exist_ok=True)
    for name in _MIGRATABLE:
        s, d = src / name, dst / name
        if not s.exists() or d.exists():
            continue
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        copied.append(name)
    return copied


def maybe_migrate_on_first_run() -> list[str]:
    """Frozen-only, opt-in: RIDIAN_MIGRATE_FROM=<legacy apps/api dir> copies
    state into APPDATA on launch. Unset (the clean-machine case) = no-op."""
    src = os.environ.get("RIDIAN_MIGRATE_FROM", "")
    if not (is_frozen() and src):
        return []
    return migrate_legacy_state(Path(src), data_dir())


def resource_base() -> Path:
    """Base for READ-ONLY bundled resources (prompt files, static/). Dev:
    apps/api (the source tree). Frozen: PyInstaller's bundle dir
    (sys._MEIPASS), where --add-data placed them at the same relative
    layout (app/agents/prompts, app/static)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return _API_DIR
