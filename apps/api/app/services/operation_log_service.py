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


# ---------------------------------------------------------------------------
# Projects — lightweight grouping for operations (v2.8)
# ---------------------------------------------------------------------------
# A project is just {id, name, created_at} in state/projects.json; operations
# carry a project_id. Assignment happens at run time (the operator picks a
# project in the sidebar before running) or retroactively via
# assign_operation_project. No nesting, no per-project settings — organizing
# only.

_PROJECTS_NAME = "projects"
_PROJECT_NAME_MAX = 60


def list_projects() -> list[dict]:
    return state_store.load_list(_PROJECTS_NAME)


def create_project(name: str) -> dict:
    """Create a project (or return the existing one with the same name,
    case-insensitively — organizing shouldn't spawn near-duplicates)."""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Project name is required.")
    if len(clean) > _PROJECT_NAME_MAX:
        clean = clean[:_PROJECT_NAME_MAX].rstrip()
    items = state_store.load_list(_PROJECTS_NAME)
    for p in items:
        if (p.get("name") or "").strip().lower() == clean.lower():
            return p
    project = {
        "id": "proj_" + uuid.uuid4().hex[:10],
        "name": clean,
        "created_at": _now_iso(),
    }
    items.insert(0, project)
    state_store.save(_PROJECTS_NAME, items)
    log.info("project.created id=%s name=%s", project["id"], clean)
    return project


def project_exists(project_id: str) -> bool:
    if not project_id:
        return False
    return any(p.get("id") == project_id for p in state_store.load_list(_PROJECTS_NAME))


def assign_operation_project(operation_id: str, project_id: str) -> Optional[dict]:
    """Set (or clear, with "") the project on a stored operation. Returns the
    updated record, or None if the operation isn't found."""
    if not operation_id:
        return None
    items = state_store.load_list(_OPS_NAME)
    for i, item in enumerate(items):
        if item.get("id") != operation_id:
            continue
        item["project_id"] = project_id or ""
        items[i] = item
        state_store.save(_OPS_NAME, items)
        log.info("operation.project_assigned id=%s project=%s", operation_id, project_id)
        return item
    return None


def upsert_operation(record: dict) -> dict:
    """Insert a finished/awaiting operation, or replace the existing entry with
    the same id in place. Used for resumable (pause→resume) operations so a run
    that asks a question and later completes appears exactly once in history,
    updated — never duplicated."""
    items = state_store.load_list(_OPS_NAME)
    oid = record.get("id")
    for i, item in enumerate(items):
        if item.get("id") == oid:
            items[i] = record
            state_store.save(_OPS_NAME, items)
            log.info("operation.upserted id=%s status=%s", oid, record.get("status"))
            return record
    items.insert(0, record)
    if len(items) > 500:
        items = items[:500]
    state_store.save(_OPS_NAME, items)
    log.info("operation.logged id=%s status=%s", oid, record.get("status"))
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
        # v1.7: missing-information requests ({id, question, context_hint})
        # and the planner's final receipt text (displayed + spoken + reloadable).
        "needs_input": [],
        "receipt": "",
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
