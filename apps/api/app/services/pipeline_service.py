"""Deals pipeline — the back-office spine (v5.0 Phase 1).

Same architecture as memory_service contacts: provenance-stamped records
(written_by / source_op via memory_service._stamp), atomic state_store
JSON, and deterministic validation that REFUSES before anything is
written — an invalid stage, value, or date raises PipelineError and the
store is untouched. No prompt-only rules: every constraint here is code.

A deal links to a contact record by id. Contacts remain the single source
of truth for email addresses (the recipient provenance gate reads them),
so the pipeline never stores addresses of its own.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any, Optional

from . import state_store
from .memory_service import _stamp  # provenance: written_by REQUIRED, validated

STAGES = ("lead", "contacted", "meeting", "proposal", "won", "lost")
ACTIVE_STAGES = ("lead", "contacted", "meeting", "proposal")

DEAL_FIELDS = ("contact_id", "contact_name", "title", "stage", "value_usd",
               "next_action", "next_action_date", "notes")

_STORE = "deals"


class PipelineError(ValueError):
    """Deterministic refusal — raised BEFORE any write happens."""


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _new_id() -> str:
    return "deal_" + uuid.uuid4().hex[:12]


def _validate_stage(stage: str) -> str:
    s = str(stage or "").strip().lower()
    if s not in STAGES:
        raise PipelineError(
            f"stage must be one of {'/'.join(STAGES)} — got {stage!r}.")
    return s


def _validate_value(value: Any) -> str:
    """Deal value in USD as a canonical string; '' = not set yet."""
    raw = str(value if value is not None else "").strip().replace("$", "").replace(",", "")
    if raw == "":
        return ""
    try:
        v = float(raw)
    except ValueError as exc:
        raise PipelineError(f"value must be a dollar amount — got {value!r}.") from exc
    if v < 0:
        raise PipelineError("value cannot be negative.")
    return f"{v:.2f}"


def _validate_date(d: Any) -> str:
    """ISO date (YYYY-MM-DD); '' = none."""
    raw = str(d or "").strip()
    if raw == "":
        return ""
    try:
        return _dt.date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise PipelineError(
            f"next_action_date must be an ISO date (YYYY-MM-DD) — got {d!r}.") from exc


def list_deals() -> list[dict]:
    return state_store.load_list(_STORE)


def add_deal(data: dict, *, written_by: str, source_op: str = "") -> dict:
    """Create a deal. Validates EVERYTHING before touching the store."""
    contact_id = str(data.get("contact_id") or "").strip()
    contact_name = str(data.get("contact_name") or "").strip()
    if not contact_id:
        raise PipelineError("a deal must link to a contact record (contact_id).")
    entry = {
        "contact_id": contact_id,
        "contact_name": contact_name,
        "title": str(data.get("title") or "").strip(),
        "stage": _validate_stage(data.get("stage") or "lead"),
        "value_usd": _validate_value(data.get("value_usd")),
        "next_action": str(data.get("next_action") or "").strip(),
        "next_action_date": _validate_date(data.get("next_action_date")),
        "notes": str(data.get("notes") or "").strip(),
    }
    now = _now_iso()
    entry["id"] = _new_id()
    entry["created_iso"] = now
    entry["updated_iso"] = now
    entry["last_touch_iso"] = ""
    entry["touches"] = []
    _stamp(entry, written_by, source_op)
    items = list_deals()
    items.insert(0, entry)
    state_store.save(_STORE, items)
    return entry


def update_deal(deal_id: str, data: dict) -> Optional[dict]:
    """Update fields on a deal. Validates BEFORE writing; None if not found."""
    updates: dict = {}
    if "stage" in data:
        updates["stage"] = _validate_stage(data["stage"])
    if "value_usd" in data:
        updates["value_usd"] = _validate_value(data["value_usd"])
    if "next_action_date" in data:
        updates["next_action_date"] = _validate_date(data["next_action_date"])
    for f in ("title", "next_action", "notes", "contact_name"):
        if f in data:
            updates[f] = str(data.get(f) or "").strip()
    items = list_deals()
    for i, d in enumerate(items):
        if d.get("id") == deal_id:
            d.update(updates)
            d["updated_iso"] = _now_iso()
            items[i] = d
            state_store.save(_STORE, items)
            return d
    return None


def log_touch(deal_id: str, note: str, *, written_by: str,
              source_op: str = "") -> Optional[dict]:
    """Append a touch (call/email/meeting note) to a deal; None if not found."""
    text = str(note or "").strip()
    if not text:
        raise PipelineError("a touch needs a note — what happened?")
    items = list_deals()
    for i, d in enumerate(items):
        if d.get("id") == deal_id:
            touch = {"when": _now_iso(), "note": text}
            _stamp(touch, written_by, source_op)
            d.setdefault("touches", []).append(touch)
            d["last_touch_iso"] = touch["when"]
            d["updated_iso"] = touch["when"]
            items[i] = d
            state_store.save(_STORE, items)
            return d
    return None


def reassign_deals(old_contact_id: str, new_contact_id: str,
                   new_contact_name: str) -> int:
    """Move every deal (touches ride along — history is never orphaned)
    from one contact to another. Merge support; returns the count moved.
    Deliberately does NOT update last_touch_iso: a merge is bookkeeping,
    not client contact, so staleness reports stay honest."""
    items = list_deals()
    moved = 0
    for d in items:
        if d.get("contact_id") == old_contact_id:
            d["contact_id"] = new_contact_id
            d["contact_name"] = str(new_contact_name or "").strip()
            d["updated_iso"] = _now_iso()
            moved += 1
    if moved:
        state_store.save(_STORE, items)
    return moved


def deals_needing_followup(days_stale: int = 7, due_within_days: int = 7,
                           today: Optional[_dt.date] = None) -> dict:
    """Read-only report: active deals gone quiet + next actions coming due.

    ``today`` is injectable for deterministic tests."""
    today = today or _dt.date.today()
    stale_before = today - _dt.timedelta(days=days_stale)
    due_by = today + _dt.timedelta(days=due_within_days)
    stale, due = [], []
    for d in list_deals():
        if d.get("stage") not in ACTIVE_STAGES:
            continue
        last = (d.get("last_touch_iso") or "")[:10]
        try:
            last_date = _dt.date.fromisoformat(last) if last else None
        except ValueError:
            last_date = None
        if last_date is None or last_date <= stale_before:
            stale.append(d)
        nad = d.get("next_action_date") or ""
        if nad:
            try:
                if _dt.date.fromisoformat(nad) <= due_by:
                    due.append(d)
            except ValueError:
                pass
    return {"stale": stale, "due": due,
            "days_stale": days_stale, "due_within_days": due_within_days}
