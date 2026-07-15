"""Memory layer for Ridian Command Center.

Holds the operator's durable context: contacts, brand voice, facts,
follow-ups, decisions. All edits are operator-driven for now; agents
gain write access in a later milestone.

Backed by ``state_store`` JSON files under ``apps/api/state/``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from . import state_store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# Write-path provenance. Every memory record is stamped at write time with
# HOW it got written — the 2026-07-15 audit had to forensically reconstruct
# this from operation logs; now it's a required, self-describing field.
# "unknown" appears only on records that predate the stamp (one-time backfill,
# 2026-07-15) — new code must never write it.
WRITTEN_BY_VALUES = ("commit", "save_memory", "manual", "unknown")


def _stamp(entry: dict, written_by: str, source_op: str) -> dict:
    """Attach provenance to a new record. ``written_by`` is REQUIRED at every
    call site (no default) so a future write path can't silently claim a
    plausible label — forgetting it is a loud TypeError, not quiet ambiguity."""
    if written_by not in WRITTEN_BY_VALUES or written_by == "unknown":
        raise ValueError(f"invalid written_by {written_by!r}")
    entry["written_by"] = written_by
    entry["source_op"] = str(source_op or "")
    return entry


def _empty_brand_section() -> dict:
    return {"voice": "", "audience": "", "do": [], "avoid": [], "notes": ""}


def _default_brand() -> dict:
    return {
        "ridian": _empty_brand_section(),
        "open_gulf": _empty_brand_section(),
        "buns": _empty_brand_section(),
    }


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

CONTACT_FIELDS = ("name", "role", "company", "email", "phone", "notes", "last_contact_iso")


def list_contacts() -> list[dict]:
    return state_store.load_list("contacts")


def add_contact(data: dict, *, written_by: str, source_op: str = "") -> dict:
    now = _now_iso()
    entry = {f: str(data.get(f, "") or "") for f in CONTACT_FIELDS}
    entry["id"] = _new_id()
    entry["created_iso"] = now
    entry["updated_iso"] = now
    _stamp(entry, written_by, source_op)
    items = list_contacts()
    items.insert(0, entry)
    state_store.save("contacts", items)
    return entry


def update_contact(contact_id: str, data: dict) -> Optional[dict]:
    items = list_contacts()
    for i, c in enumerate(items):
        if c.get("id") == contact_id:
            for f in CONTACT_FIELDS:
                if f in data:
                    c[f] = str(data.get(f, "") or "")
            c["updated_iso"] = _now_iso()
            items[i] = c
            state_store.save("contacts", items)
            return c
    return None


def delete_contact(contact_id: str) -> bool:
    items = list_contacts()
    new_items = [c for c in items if c.get("id") != contact_id]
    if len(new_items) == len(items):
        return False
    state_store.save("contacts", new_items)
    return True


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

FACT_FIELDS = ("topic", "fact", "source")


def list_facts() -> list[dict]:
    return state_store.load_list("facts")


def add_fact(data: dict, *, written_by: str, source_op: str = "") -> dict:
    entry = {f: str(data.get(f, "") or "") for f in FACT_FIELDS}
    entry["id"] = _new_id()
    entry["created_iso"] = _now_iso()
    _stamp(entry, written_by, source_op)
    items = list_facts()
    items.insert(0, entry)
    state_store.save("facts", items)
    return entry


def delete_fact(fact_id: str) -> bool:
    items = list_facts()
    new_items = [c for c in items if c.get("id") != fact_id]
    if len(new_items) == len(items):
        return False
    state_store.save("facts", new_items)
    return True


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------

FOLLOW_UP_FIELDS = ("what", "who", "due_iso", "status", "source_run")
ALLOWED_FOLLOW_UP_STATUSES = ("open", "done")


def list_follow_ups() -> list[dict]:
    return state_store.load_list("follow_ups")


def list_open_follow_ups() -> list[dict]:
    return [f for f in list_follow_ups() if f.get("status") != "done"]


def add_follow_up(data: dict, *, written_by: str, source_op: str = "") -> dict:
    now = _now_iso()
    entry = {f: str(data.get(f, "") or "") for f in FOLLOW_UP_FIELDS}
    if entry["status"] not in ALLOWED_FOLLOW_UP_STATUSES:
        entry["status"] = "open"
    entry["id"] = _new_id()
    entry["created_iso"] = now
    entry["updated_iso"] = now
    _stamp(entry, written_by, source_op)
    items = list_follow_ups()
    items.insert(0, entry)
    state_store.save("follow_ups", items)
    return entry


def update_follow_up(follow_up_id: str, data: dict) -> Optional[dict]:
    items = list_follow_ups()
    for i, f in enumerate(items):
        if f.get("id") == follow_up_id:
            for field in FOLLOW_UP_FIELDS:
                if field in data:
                    val = str(data.get(field, "") or "")
                    if field == "status" and val not in ALLOWED_FOLLOW_UP_STATUSES:
                        continue
                    f[field] = val
            f["updated_iso"] = _now_iso()
            items[i] = f
            state_store.save("follow_ups", items)
            return f
    return None


def delete_follow_up(follow_up_id: str) -> bool:
    items = list_follow_ups()
    new_items = [c for c in items if c.get("id") != follow_up_id]
    if len(new_items) == len(items):
        return False
    state_store.save("follow_ups", new_items)
    return True


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

DECISION_FIELDS = ("decision", "context", "date_iso")


def list_decisions() -> list[dict]:
    return state_store.load_list("decisions")


def add_decision(data: dict, *, written_by: str, source_op: str = "") -> dict:
    entry = {f: str(data.get(f, "") or "") for f in DECISION_FIELDS}
    if not entry["date_iso"]:
        entry["date_iso"] = _now_iso()
    entry["id"] = _new_id()
    entry["created_iso"] = _now_iso()
    _stamp(entry, written_by, source_op)
    items = list_decisions()
    items.insert(0, entry)
    state_store.save("decisions", items)
    return entry


def delete_decision(decision_id: str) -> bool:
    items = list_decisions()
    new_items = [c for c in items if c.get("id") != decision_id]
    if len(new_items) == len(items):
        return False
    state_store.save("decisions", new_items)
    return True


# ---------------------------------------------------------------------------
# Brand (single object with 3 known sections)
# ---------------------------------------------------------------------------

BRAND_KEYS = ("ridian", "open_gulf", "buns")
BRAND_SECTION_FIELDS = ("voice", "audience", "do", "avoid", "notes")


def get_brand() -> dict:
    data = state_store.load_dict("brand", default=_default_brand())
    # Ensure all 3 keys + all section fields exist (defensive merge).
    for k in BRAND_KEYS:
        section = data.get(k, {}) if isinstance(data.get(k), dict) else {}
        merged = _empty_brand_section()
        merged.update({f: section.get(f, merged[f]) for f in BRAND_SECTION_FIELDS})
        data[k] = merged
    return data


def save_brand(data: dict) -> dict:
    current = get_brand()
    for k in BRAND_KEYS:
        if k not in data or not isinstance(data[k], dict):
            continue
        section = current[k]
        incoming = data[k]
        for f in BRAND_SECTION_FIELDS:
            if f in incoming:
                val = incoming[f]
                if f in ("do", "avoid"):
                    section[f] = [str(x) for x in val] if isinstance(val, list) else []
                else:
                    section[f] = str(val or "")
        current[k] = section
    state_store.save("brand", current)
    return current


# ---------------------------------------------------------------------------
# Operator Profile (v1.6)
# ---------------------------------------------------------------------------
# Who the operator is and what the business is trying to do. This is the
# context that turns generic research output into operator-specific moves.
# Single dict, like brand. All fields are free text.

PROFILE_FIELDS = (
    "operator",    # who you are (name & role)
    "business",    # what your business does
    "offerings",   # what you sell
    "customers",   # who buys it (ICP)
    "goal",        # this quarter's #1 goal
    "avoid",       # topics / work you're NOT interested in
    "notes",       # anything else
)


def _default_profile() -> dict:
    return {f: "" for f in PROFILE_FIELDS}


def get_profile() -> dict:
    data = state_store.load_dict("profile", default=_default_profile())
    merged = _default_profile()
    for f in PROFILE_FIELDS:
        merged[f] = str(data.get(f, "") or "")
    return merged


def save_profile(updates: dict) -> dict:
    current = get_profile()
    for f in PROFILE_FIELDS:
        if f in updates:
            current[f] = str(updates.get(f, "") or "")
    state_store.save("profile", current)
    return current


def profile_is_set() -> bool:
    """True when at least the business or goal field has content."""
    p = get_profile()
    return bool((p.get("business") or "").strip() or (p.get("goal") or "").strip())


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def memory_summary() -> dict:
    """Counts used by the dashboard and other aggregators."""
    return {
        "contacts": len(list_contacts()),
        "facts": len(list_facts()),
        "open_follow_ups": len(list_open_follow_ups()),
        "decisions": len(list_decisions()),
    }
