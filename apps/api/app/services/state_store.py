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

import datetime as _dt
import json
import logging
import os
import re as _re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("ridian.state")

from .runtime_paths import data_dir

# v4.2: dev -> apps/api/state/ exactly as before; frozen ->
# %APPDATA%/Ridian Operator/state/ (memory store, operations/spend ledger,
# projects, suggestion dismissals, logs — everything writable).
STATE_DIR = data_dir() / "state"


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
    """Write a state file atomically. Returns the data that was written.

    v6.0 Phase 1: every write is preceded by a full snapshot of the CURRENT
    state (contacts, deals, memory, profile, everything under STATE_DIR),
    so any write can be undone by restoring the newest backup. The last
    SNAPSHOT_KEEP snapshots are retained."""
    snapshot(reason=f"pre-write {name}")
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


# ---------------------------------------------------------------------------
# Snapshots (v6.0 Phase 1 — backup & restore)
# ---------------------------------------------------------------------------
# A snapshot is a directory under STATE_DIR/backups/<id>/ holding a copy of
# every state JSON file plus meta.json (when, why, per-store record counts).
# save() snapshots BEFORE writing, so the newest backup is always the state
# as it stood before the most recent change. Rotation keeps the newest
# SNAPSHOT_KEEP. restore_snapshot() itself takes a pre-restore snapshot, so
# even a wrong restore is undoable.

_BACKUPS_DIRNAME = "backups"
SNAPSHOT_KEEP = 30
_SNAP_ID_RE = _re.compile(r"^\d{8}-\d{6}-\d{6}(-\d+)?$")


def _backups_dir() -> Path:
    return STATE_DIR / _BACKUPS_DIRNAME


def _state_files() -> list[Path]:
    if not STATE_DIR.exists():
        return []
    return sorted(p for p in STATE_DIR.glob("*.json") if p.is_file())


def _store_counts(files: list[Path]) -> dict:
    """{store_name: record count} — lists count entries, dicts count as 1,
    unreadable as -1. This is what previews and meta.json report."""
    out: dict = {}
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out[p.stem] = len(data) if isinstance(data, list) else 1
        except (json.JSONDecodeError, OSError):
            out[p.stem] = -1
    return out


def snapshot(reason: str = "") -> Optional[str]:
    """Copy every state file into a new timestamped backup. Returns the
    snapshot id, or None when there is no state to protect yet."""
    files = _state_files()
    if not files:
        return None
    now = _dt.datetime.now()
    base = now.strftime("%Y%m%d-%H%M%S-%f")
    bdir = _backups_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    snap_id, n = base, 0
    while (bdir / snap_id).exists():
        n += 1
        snap_id = f"{base}-{n}"
    # Build complete under a dot-name, then rename — a crash mid-copy never
    # leaves a directory that looks like a valid backup.
    tmp = bdir / f".{snap_id}.building"
    tmp.mkdir()
    for p in files:
        shutil.copy2(p, tmp / p.name)
    meta = {"id": snap_id, "created_iso": now.isoformat(timespec="seconds"),
            "reason": str(reason or ""), "stores": _store_counts(files)}
    (tmp / "meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    tmp.rename(bdir / snap_id)
    _rotate_snapshots()
    log.info("state.snapshot id=%s reason=%s", snap_id, reason)
    return snap_id


def _rotate_snapshots(keep: int = None) -> None:
    keep = SNAPSHOT_KEEP if keep is None else keep
    bdir = _backups_dir()
    if not bdir.exists():
        return
    snaps = sorted((d for d in bdir.iterdir()
                    if d.is_dir() and _SNAP_ID_RE.match(d.name)),
                   key=lambda d: d.name)          # ids sort chronologically
    for stale in snaps[:-keep] if keep else snaps:
        shutil.rmtree(stale, ignore_errors=True)


def list_snapshots() -> list[dict]:
    """All retained snapshots, newest first: {id, created_iso, reason,
    stores: {name: count}}."""
    bdir = _backups_dir()
    if not bdir.exists():
        return []
    out = []
    for d in sorted((d for d in bdir.iterdir()
                     if d.is_dir() and _SNAP_ID_RE.match(d.name)),
                    key=lambda d: d.name, reverse=True):
        meta_path = d / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {"id": d.name, "created_iso": "", "reason": "",
                    "stores": _store_counts(
                        [p for p in d.glob("*.json") if p.name != "meta.json"])}
        out.append(meta)
    return out


def get_snapshot(snap_id: str) -> Optional[dict]:
    """Meta for one snapshot, or None. Rejects malformed ids (the id is the
    directory name — never let it path-traverse)."""
    if not _SNAP_ID_RE.match(str(snap_id or "")):
        return None
    for meta in list_snapshots():
        if meta.get("id") == snap_id:
            return meta
    return None


def restore_snapshot(snap_id: str) -> dict:
    """Replace ALL current state with the snapshot's contents. DESTRUCTIVE —
    callers gate this behind operator approval. A pre-restore snapshot is
    taken first, so the state being destroyed is itself recoverable."""
    if not _SNAP_ID_RE.match(str(snap_id or "")):
        raise ValueError(f"invalid snapshot id {snap_id!r}")
    src = _backups_dir() / snap_id
    if not src.is_dir():
        raise ValueError(f"no snapshot {snap_id!r}")
    pre = snapshot(reason=f"pre-restore {snap_id}")
    for p in _state_files():          # stores created after the snapshot go too
        p.unlink()
    restored = []
    for f in sorted(src.glob("*.json")):
        if f.name == "meta.json":
            continue
        shutil.copy2(f, STATE_DIR / f.name)
        restored.append(f.stem)
    log.info("state.restored id=%s stores=%d pre=%s", snap_id, len(restored), pre)
    return {"restored": snap_id, "stores": restored,
            "pre_restore_backup": pre}
