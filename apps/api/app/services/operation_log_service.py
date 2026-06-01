"""Operation log persistence for Operator v1.

Each completed (or failed) operation appends one record to ``operations.json``
under ``apps/api/state/`` so the renderer can show recent operations and so
the planner can read prior operations for context in future milestones.

Schema (per record):

    {
      "id":              "op_<12hex>",
      "command":         "<verbatim user input>",
      "intent":          "agi-audiobook" | "unknown",
      "artifact_folder": "<outputs/<run>>",
      "started_at":      "<ISO>",
      "completed_at":    "<ISO>",
      "status":          "completed" | "failed" | "partial",
      "steps":           [{ "name", "status", "started_at",
                            "completed_at", "detail" }, ...],
      "tools_used":      ["web_search", "tts", "write_file", ...],
      "sources_count":   <int>,
      "audio_generated": true|false,
      "audio_duration_seconds": <int>,
      "artifacts":       [{ "name", "path", "kind" }, ...],
      "errors":          ["<message>", ...]
    }
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from . import state_store

log = logging.getLogger("ridian.operation_log")

_OPS_NAME = "operations"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_operation_id() -> str:
    return "op_" + uuid.uuid4().hex[:12]


def append_operation(record: dict) -> dict:
    """Append a finished operation record. Newest entries first."""
    items = state_store.load_list(_OPS_NAME)
    items.insert(0, record)
    # Cap history at 500 entries so the log file never balloons.
    if len(items) > 500:
        items = items[:500]
    state_store.save(_OPS_NAME, items)
    log.info("operation.logged id=%s status=%s", record.get("id"), record.get("status"))
    return record


def list_recent(limit: int = 20) -> list[dict]:
    items = state_store.load_list(_OPS_NAME)
    return items[: max(1, int(limit))]


def get_operation(operation_id: str) -> Optional[dict]:
    if not operation_id:
        return None
    for item in state_store.load_list(_OPS_NAME):
        if item.get("id") == operation_id:
            return item
    return None


def build_record(*, command: str, intent: str, artifact_folder: str) -> dict:
    """Initialize an in-flight operation record. Caller fills in steps/etc."""
    return {
        "id": new_operation_id(),
        "command": command,
        "intent": intent,
        "artifact_folder": artifact_folder,
        "started_at": _now_iso(),
        "completed_at": "",
        "status": "running",
        "steps": [],
        # v1.2: memory proposals from the planner — each carries its own
        # {id, kind, payload, reason, status} so reloaded runs don't re-prompt.
        "proposed_memory_updates": [],
        "tools_used": [],
        "sources_count": 0,
        "audio_generated": False,
        "audio_duration_seconds": 0,
        "artifacts": [],
        "errors": [],
    }


def finalize(record: dict, *, status: str) -> dict:
    record["status"] = status
    record["completed_at"] = _now_iso()
    return record


def update_proposal_statuses(
    operation_id: str,
    *,
    statuses: dict[str, str],
) -> Optional[dict]:
    """Flip ``status`` on memory proposals inside a stored operation.

    Used by POST /operations/{id}/memory/commit to mark each proposal as
    "committed" or "dismissed". Returns the updated operation record, or
    None if the operation id isn't found. Allowed status values are
    enforced by the caller; this helper writes whatever it's given so it
    stays a thin, testable persistence primitive.
    """
    if not operation_id or not statuses:
        return None
    items = state_store.load_list(_OPS_NAME)
    for i, item in enumerate(items):
        if item.get("id") != operation_id:
            continue
        proposals = item.get("proposed_memory_updates", [])
        for prop in proposals:
            new_status = statuses.get(prop.get("id", ""))
            if new_status:
                prop["status"] = new_status
        items[i] = item
        state_store.save(_OPS_NAME, items)
        log.info(
            "operation.proposals_updated id=%s updates=%d",
            operation_id, len(statuses),
        )
        return item
    return None
