"""Durable parked runs (v7.6, 0.9.16).

A run that parks on a question or a gate approval keeps its live session in
``operator_service._SESSIONS``, which dies with the process. On 2026-09-24
op_3bb0dfb95e13 (an Owner Workspace job) parked on a question at 10:34:51;
the app was quit and relaunched at 11:49:00, and the 11:53 answer found no
session: "That operation is no longer active".

Every park now also writes ``<state>/parked/<operation_id>.json`` holding
what ``continue_operation`` needs to rebuild the session exactly: the full
in-flight operation record (every gate flag lives there), the run folder,
the planner system prompt, the mirrored Anthropic conversation, the
upload-state line, and the OperatorContext text caches. The file is deleted
when the run ends, is dismissed, or is answered from the Approvals inbox.

``state`` is "parked" while the run waits, and "resuming" from the moment
an answer is accepted until the run parks again or ends. A "resuming" file
found after a restart means the app closed mid-run: the run cannot pick up
from the middle, so it expires rather than replaying from the question.

v7.8 (0.9.18): "running" is written when a run starts, so a run the app
closes in the middle of a step expires honestly after the restart even if
it never parked. "checkpoint" is written when the app closes between steps
(operator_service.drain): the step finished and was mirrored, and the run
resumes from exactly there after the restart.

Local only: never synced, exported, logged or snapshotted (the backups
copy only the top-level ``state/*.json``).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import state_store
from .runtime_paths import guard_real_state_write

log = logging.getLogger("ridian.parked_runs")

VERSION = 1
PARKED = "parked"
RESUMING = "resuming"
RUNNING = "running"          # v7.8: mid-step — a restart expires it
CHECKPOINT = "checkpoint"    # v7.8: stopped at a step boundary — a restart resumes it
_STATES = (PARKED, RESUMING, RUNNING, CHECKPOINT)

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _dir() -> Path:
    # Resolved per call so a test that re-points STATE_DIR isolates this too.
    return state_store.STATE_DIR / "parked"


def _path(operation_id: str) -> Optional[Path]:
    oid = str(operation_id or "")
    if not _ID_RE.fullmatch(oid) or oid.startswith("."):
        return None
    return _dir() / f"{oid}.json"


def json_safe(value: Any) -> Any:
    """Plain JSON for the record and the mirrored conversation. SDK blocks
    are dumped exactly as the SDK itself sends them back to the API, so a
    restored history (thinking signatures included) replays unchanged."""
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump(
            mode="json", exclude_unset=True, by_alias=True,
            exclude=getattr(value, "__api_exclude__", None)))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((json_safe(v) for v in value), key=str)
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def save(session: Any, state: str = PARKED) -> bool:
    """Write (or rewrite) the run's parked file atomically. Never raises: a
    failed write leaves the in-memory session working, and the startup
    sweep expires the run honestly if the app restarts before it ends."""
    operator = session.operator
    record = operator.record
    path = _path(record.get("id"))
    if path is None:
        return False
    payload = {
        "version": VERSION,
        "state": state,
        "operation_id": record["id"],
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "folder": str(session.folder),
        "system": session.system,
        "upload_state_line": session.upload_state_line,
        "provider": str(getattr(session, "provider", "anthropic") or "anthropic"),
        "sources_packet_text": operator.sources_packet_text or "",
        "script_text": operator.script_text or "",
        "record": json_safe(record),
        "input_list": json_safe(session.input_list or []),
    }
    tmp = path.with_name("." + path.name + ".tmp")
    try:
        guard_real_state_write(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 — a park never fails on its backup
        log.warning("parked_runs.save_failed id=%s type=%s", record.get("id"), type(exc).__name__)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def load(operation_id: str) -> Optional[dict]:
    """The parked payload, or None when there is none or it is unusable
    (unreadable, another version, or not this run's)."""
    path = _path(operation_id)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("parked_runs.unreadable id=%s type=%s", operation_id, type(exc).__name__)
        return None
    record = data.get("record") if isinstance(data, dict) else None
    if (data.get("version") != VERSION
            or data.get("operation_id") != operation_id
            or data.get("state") not in _STATES
            or not isinstance(record, dict) or record.get("id") != operation_id
            or not isinstance(data.get("input_list"), list)
            or not isinstance(data.get("system"), str)
            or not isinstance(data.get("folder"), str) or not data["folder"]):
        log.warning("parked_runs.invalid id=%s", operation_id)
        return None
    return data


def exists(operation_id: str) -> bool:
    path = _path(operation_id)
    return path is not None and path.is_file()


def delete(operation_id: str) -> None:
    path = _path(operation_id)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("parked_runs.delete_failed id=%s type=%s", operation_id, type(exc).__name__)


def list_ids() -> list[str]:
    try:
        return sorted(p.stem for p in _dir().glob("*.json") if p.is_file())
    except OSError:
        return []
