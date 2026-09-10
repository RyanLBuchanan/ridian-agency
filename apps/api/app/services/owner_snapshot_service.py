"""Ridian Owner Snapshot v1 — a local, read-only, allowlisted export.

WHY THIS EXISTS. The Owner Workspace on ridiantechnologies.com needs
authentic Operator context (recent work, projects, obligations, approvals,
the shape of the morning brief) to populate its read-only home — without the
website ever gaining execution authority over this PC, and without a single
credential, transcript, memory record, audit row, companion record, or local
path leaving the machine. This module writes that file. Nothing here
uploads; the operator inspects the file and moves it by hand.

THREE PINS (each has a test in tests/test_owner_snapshot.py):

  1. SOURCES — only the named service functions are consulted. An AST
     allowlist pins both this module's imports and every attribute it
     touches on them. No JSON file is opened here; the services own their
     stores, and this module cannot name a writer.
  2. FIELDS — every record type has an explicit (source field, output key,
     kind) table. A field not named is a field not exported. DENY_SOURCE_FIELDS
     lists what must never be mapped (emails, phones, notes, dollar values,
     steps, receipts, paths, planner kwargs, provenance stamps, ...);
     build_snapshot() refuses to run if a table ever names one of them, and
     refuses to emit any key matching FORBIDDEN_KEY_RE or any string carrying
     a drive-letter / UNC / home path or the data directory.
  3. NO WRITES except the export file itself, under <data_dir>/exports/.
     The state store is byte-identical before and after an export.

Record timestamps are passed through as the Operator stores them: naive
local-time ISO 8601 strings. ``generatedAt`` is UTC; ``source.localUtcOffset``
lets the importer interpret the local ones.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import (approval_inbox_service, brief_service, dashboard_service,
               memory_service, obligations_service, operation_log_service,
               pipeline_service, project_service)
from .runtime_paths import data_dir

log = logging.getLogger("ridian.owner_snapshot")

SCHEMA = "ridian-operator-snapshot"
VERSION = 1
APPLICATION = "Ridian Operator"
SCHEMA_ID = "https://ridiantechnologies.com/schemas/owner-snapshot-v1.schema.json"
EXPORTS_DIRNAME = "exports"
FILE_PREFIX = "owner-snapshot-"

RECENT_WORK_LIMIT = 25
LEGACY_PROJECT_LIMIT = 30
LIST_LIMIT = 200            # contacts, deals, obligations, follow-ups, approvals
TEXT_MAX = 2000
LIST_ITEM_MAX = 50

# Output keys may never match this — the website importer enforces the same
# pattern, so a key that slipped past here would be rejected there too.
FORBIDDEN_KEY_RE = re.compile(
    r"token|secret|key|password|credential|cookie|auth", re.IGNORECASE)

PATH_PLACEHOLDER = "[local path removed]"
_DRIVE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>|]*")
_UNC_PATH_RE = re.compile(r"\\\\[^\s\"'<>|]+")
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/[^\s\"'<>|]*")

# Source fields that no table may ever name. The intersection check in
# check_policy() is what makes a mutated table fail loudly instead of
# silently exporting a phone number.
DENY_SOURCE_FIELDS: frozenset[str] = frozenset({
    # contact / deal / follow-up detail that is not the website's business
    "email", "phone", "notes", "source", "value_usd", "touches",
    # operation internals: transcripts, planner state, provenance, money fences
    "steps", "receipt", "needs_input", "proposed_memory_updates",
    "source_titles", "errors", "artifacts", "artifact_folder", "path",
    "reconciliation", "research_approved", "research_declined",
    "audio_generated", "audio_duration_seconds", "cost_ceiling_usd",
    # approval internals: planner kwargs, offered options, gate evidence
    "kwargs", "options", "gate_flags", "user_stated_numbers",
    "user_provided_emails", "folder", "answered_at", "outcome",
    # obligation internals: the runnable command and dismissal bookkeeping
    "task", "dismissed_period",
    # provenance stamps stay on the PC
    "written_by", "source_op", "source_run",
})

_STR, _BOOL, _INT, _FLOAT, _STRLIST = "str", "bool", "int", "float", "strlist"
_KINDS = frozenset({_STR, _BOOL, _INT, _FLOAT, _STRLIST})

# (source field, output key, kind) — the complete export for each record.
RECENT_WORK_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("command", "command", _STR), ("intent", "intent", _STR),
    ("status", "status", _STR), ("started_at", "startedAt", _STR),
    ("completed_at", "completedAt", _STR), ("spend_usd", "spendUsd", _FLOAT),
    ("tools_used", "toolsUsed", _STRLIST), ("project_id", "projectId", _STR),
    ("background", "background", _BOOL), ("awaiting_input", "awaitingInput", _BOOL),
    ("sources_count", "sourcesCount", _INT),
)
PROJECT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("name", "name", _STR), ("created_at", "createdAt", _STR),
    ("parent_id", "parentId", _STR),
)
LEGACY_PROJECT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("name", "name", _STR), ("workflow", "workflow", _STR),
    ("channel", "channel", _STR), ("mtime_iso", "modifiedAt", _STR),
    ("pinned", "pinned", _BOOL),
)
OBLIGATION_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("name", "name", _STR),
    ("last_completed_period", "lastCompletedPeriod", _STR),
    ("created_iso", "createdAt", _STR), ("updated_iso", "updatedAt", _STR),
)
CADENCE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("kind", "kind", _STR), ("day", "day", _STR), ("weekday", "weekday", _STR),
    ("date", "date", _STR),
)
DUE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("due_date", "dueDate", _STR), ("status", "status", _STR),
    ("days_overdue", "daysOverdue", _INT), ("missed_periods", "missedPeriods", _INT),
)
APPROVAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("operation_id", "operationId", _STR),
    ("command", "command", _STR), ("tool", "tool", _STR),
    ("reason", "reason", _STR), ("question", "question", _STR),
    ("staged_at", "stagedAt", _STR), ("status", "status", _STR),
    ("stale", "stale", _BOOL),
)
CONTACT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("name", "name", _STR), ("role", "role", _STR),
    ("company", "company", _STR), ("last_contact_iso", "lastContactAt", _STR),
    ("created_iso", "createdAt", _STR), ("updated_iso", "updatedAt", _STR),
)
DEAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("title", "title", _STR), ("stage", "stage", _STR),
    ("contact_id", "contactId", _STR), ("contact_name", "contactName", _STR),
    ("next_action", "nextAction", _STR), ("next_action_date", "nextActionDate", _STR),
    ("created_iso", "createdAt", _STR), ("updated_iso", "updatedAt", _STR),
    ("last_touch_iso", "lastTouchAt", _STR),
)
FOLLOW_UP_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("what", "what", _STR), ("who", "who", _STR),
    ("due_iso", "dueAt", _STR), ("status", "status", _STR),
    ("created_iso", "createdAt", _STR), ("updated_iso", "updatedAt", _STR),
)
# Morning-brief sections are exported as counts and availability only —
# never their rows (those carry invoice balances, thread subjects, senders).
BRIEF_SECTIONS: tuple[str, ...] = (
    "obligations_due", "today_events", "needs_reply", "due_today",
    "due_this_week", "stale_deals", "unpaid_invoices", "awaiting_approval",
    "ridian_noticed",
)


def _tables() -> dict[str, tuple[tuple[str, str, str], ...]]:
    """Read at call time so a mutated table is seen by check_policy()."""
    return {
        "recentWork": RECENT_WORK_FIELDS, "projects": PROJECT_FIELDS,
        "legacyProjects": LEGACY_PROJECT_FIELDS, "obligations": OBLIGATION_FIELDS,
        "cadence": CADENCE_FIELDS, "due": DUE_FIELDS, "approvals": APPROVAL_FIELDS,
        "contacts": CONTACT_FIELDS, "deals": DEAL_FIELDS, "followUps": FOLLOW_UP_FIELDS,
    }


class SnapshotPolicyError(RuntimeError):
    """The export refused itself: a table names a denied field, an output
    key matches the forbidden pattern, or a value carries a local path."""


# ---------------------------------------------------------------------------
# Scrubbing + coercion
# ---------------------------------------------------------------------------

def _scrub(text: str, place_needles: tuple[str, ...]) -> str:
    for needle in place_needles:
        if needle:
            text = re.sub(re.escape(needle), PATH_PLACEHOLDER, text,
                          flags=re.IGNORECASE)
    text = _DRIVE_PATH_RE.sub(PATH_PLACEHOLDER, text)
    text = _UNC_PATH_RE.sub(PATH_PLACEHOLDER, text)
    text = _HOME_PATH_RE.sub(PATH_PLACEHOLDER, text)
    return text


def _place_needles() -> tuple[str, ...]:
    """Every spelling of the data directory that could appear in free text."""
    d = str(data_dir())
    return tuple({d, d.replace("\\", "/"), d.replace("/", "\\")})


class _Coercer:
    def __init__(self) -> None:
        self._needles = _place_needles()

    def text(self, value: Any) -> str:
        if value is None:
            return ""
        s = value if isinstance(value, str) else str(value)
        return _scrub(s[:TEXT_MAX], self._needles)

    def coerce(self, value: Any, kind: str) -> Any:
        if kind == _STR:
            return self.text(value)
        if kind == _BOOL:
            return bool(value)
        if kind == _INT:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0
        if kind == _FLOAT:
            try:
                return round(float(value or 0.0), 4)
            except (TypeError, ValueError):
                return 0.0
        if kind == _STRLIST:
            if not isinstance(value, list):
                return []
            return [self.text(x) for x in value
                    if isinstance(x, (str, int, float))][:LIST_ITEM_MAX]
        raise SnapshotPolicyError(f"unknown field kind {kind!r}")

    def pick(self, record: Any, table: tuple[tuple[str, str, str], ...]) -> dict:
        src = record if isinstance(record, dict) else {}
        return {dst: self.coerce(src.get(field), kind) for field, dst, kind in table}


# ---------------------------------------------------------------------------
# Record builders — one per section, reading ONLY the named services
# ---------------------------------------------------------------------------

def _recent_work(c: _Coercer) -> list[dict]:
    out = []
    for op in operation_log_service.list_recent(limit=RECENT_WORK_LIMIT):
        if not isinstance(op, dict):
            continue
        row = c.pick(op, RECENT_WORK_FIELDS)
        artifacts = op.get("artifacts") if isinstance(op.get("artifacts"), list) else []
        # Filenames only — the artifact's local path is never read.
        row["artifactNames"] = [
            c.text(os.path.basename(str(a.get("name"))))
            for a in artifacts if isinstance(a, dict) and a.get("name")
        ][:LIST_ITEM_MAX]
        needs = op.get("needs_input")
        row["openQuestions"] = len(needs) if isinstance(needs, list) else 0
        errors = op.get("errors")
        row["errorCount"] = len(errors) if isinstance(errors, list) else 0
        out.append(row)
    return out


def _projects(c: _Coercer) -> list[dict]:
    return [c.pick(p, PROJECT_FIELDS)
            for p in operation_log_service.list_projects()[:LIST_LIMIT]
            if isinstance(p, dict)]


def _legacy_projects(c: _Coercer) -> list[dict]:
    try:
        runs = project_service.list_recent_projects(limit=LEGACY_PROJECT_LIMIT)
    except Exception as exc:  # noqa: BLE001 — a missing outputs tree is not a failure
        log.info("owner_snapshot.legacy_projects_unavailable %s", type(exc).__name__)
        runs = []
    return [c.pick(r, LEGACY_PROJECT_FIELDS) for r in runs if isinstance(r, dict)]


def _obligations(c: _Coercer) -> list[dict]:
    out = []
    for ob in obligations_service.list_obligations()[:LIST_LIMIT]:
        if not isinstance(ob, dict):
            continue
        row = c.pick(ob, OBLIGATION_FIELDS)
        row["cadence"] = c.pick(ob.get("cadence"), CADENCE_FIELDS)
        try:
            row["nextDue"] = c.text(obligations_service.next_due(ob))
        except Exception:  # noqa: BLE001 — malformed cadence: honest blank
            row["nextDue"] = ""
        try:
            due = obligations_service.due_status(ob)
        except Exception:  # noqa: BLE001
            due = None
        row["due"] = c.pick(due, DUE_FIELDS) if isinstance(due, dict) else None
        out.append(row)
    return out


def _approvals(c: _Coercer) -> list[dict]:
    out = []
    for a in approval_inbox_service.list_pending()[:LIST_LIMIT]:
        if not isinstance(a, dict):
            continue
        row = c.pick(a, APPROVAL_FIELDS)
        options = a.get("options")
        row["optionCount"] = len(options) if isinstance(options, list) else 0
        out.append(row)
    return out


def _contacts(c: _Coercer) -> list[dict]:
    return [c.pick(r, CONTACT_FIELDS)
            for r in memory_service.list_contacts()[:LIST_LIMIT]
            if isinstance(r, dict)]


def _deals(c: _Coercer) -> list[dict]:
    out = []
    for d in pipeline_service.list_deals()[:LIST_LIMIT]:
        if not isinstance(d, dict):
            continue
        row = c.pick(d, DEAL_FIELDS)
        touches = d.get("touches")
        row["touchCount"] = len(touches) if isinstance(touches, list) else 0
        row["active"] = d.get("stage") in pipeline_service.ACTIVE_STAGES
        out.append(row)
    return out


def _follow_ups(c: _Coercer) -> list[dict]:
    dashboard = dashboard_service.build_dashboard(recent_limit=1)
    items = dashboard.get("open_follow_ups") if isinstance(dashboard, dict) else []
    return [c.pick(f, FOLLOW_UP_FIELDS)
            for f in (items or [])[:LIST_LIMIT] if isinstance(f, dict)]


def _empty_sections() -> dict:
    return {name: {"count": 0, "empty": True, "unavailable": True}
            for name in BRIEF_SECTIONS}


def _morning_brief(c: _Coercer) -> dict:
    """Headings and counts only. The brief's rows carry invoice balances,
    thread subjects and senders, and deal values — none of that travels."""
    try:
        brief = brief_service.build_brief()
    except Exception as exc:  # noqa: BLE001 — honest unavailability
        log.info("owner_snapshot.brief_unavailable %s", type(exc).__name__)
        return {"available": False, "generatedFor": "", "sections": _empty_sections()}
    sections_in = brief.get("sections") if isinstance(brief, dict) else {}
    sections = _empty_sections()
    for name in BRIEF_SECTIONS:
        sec = (sections_in or {}).get(name)
        if not isinstance(sec, dict):
            continue
        items = sec.get("items")
        sections[name] = {
            "count": len(items) if isinstance(items, list) else 0,
            "empty": bool(sec.get("empty", not items)),
            "unavailable": bool(sec.get("unavailable", False)),
        }
    return {"available": True,
            "generatedFor": c.text((brief or {}).get("generated_for")),
            "sections": sections}


# ---------------------------------------------------------------------------
# The contract (pydantic is the single source of truth for the JSON Schema)
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Source(_Strict):
    application: str
    version: str
    localUtcOffset: str


class Summary(_Strict):
    recentWork: int
    projects: int
    legacyProjects: int
    obligations: int
    obligationsDue: int
    approvalsPending: int
    approvalsStale: int
    contacts: int
    deals: int
    dealsActive: int
    followUpsOpen: int


class RecentWork(_Strict):
    id: str
    command: str
    intent: str
    status: str
    startedAt: str
    completedAt: str
    spendUsd: float
    toolsUsed: list[str]
    projectId: str
    background: bool
    awaitingInput: bool
    sourcesCount: int
    artifactNames: list[str]
    openQuestions: int
    errorCount: int


class Project(_Strict):
    id: str
    name: str
    createdAt: str
    parentId: str


class LegacyProject(_Strict):
    name: str
    workflow: str
    channel: str
    modifiedAt: str
    pinned: bool


class Cadence(_Strict):
    kind: str
    day: str
    weekday: str
    date: str


class Due(_Strict):
    dueDate: str
    status: str
    daysOverdue: int
    missedPeriods: int


class Obligation(_Strict):
    id: str
    name: str
    lastCompletedPeriod: str
    createdAt: str
    updatedAt: str
    cadence: Cadence
    nextDue: str
    due: Optional[Due]


class Approval(_Strict):
    id: str
    operationId: str
    command: str
    tool: str
    reason: str
    question: str
    stagedAt: str
    status: str
    stale: bool
    optionCount: int


class Contact(_Strict):
    id: str
    name: str
    role: str
    company: str
    lastContactAt: str
    createdAt: str
    updatedAt: str


class Deal(_Strict):
    id: str
    title: str
    stage: str
    contactId: str
    contactName: str
    nextAction: str
    nextActionDate: str
    createdAt: str
    updatedAt: str
    lastTouchAt: str
    touchCount: int
    active: bool


class FollowUp(_Strict):
    id: str
    what: str
    who: str
    dueAt: str
    status: str
    createdAt: str
    updatedAt: str


class SectionCount(_Strict):
    count: int
    empty: bool
    unavailable: bool


class BriefSections(_Strict):
    obligations_due: SectionCount
    today_events: SectionCount
    needs_reply: SectionCount
    due_today: SectionCount
    due_this_week: SectionCount
    stale_deals: SectionCount
    unpaid_invoices: SectionCount
    awaiting_approval: SectionCount
    ridian_noticed: SectionCount


class MorningBrief(_Strict):
    available: bool
    generatedFor: str
    sections: BriefSections


class OwnerSnapshotV1(_Strict):
    schema_: Literal["ridian-operator-snapshot"] = Field(alias="schema")
    version: Literal[1]
    generatedAt: str
    source: Source
    summary: Summary
    recentWork: list[RecentWork]
    projects: list[Project]
    legacyProjects: list[LegacyProject]
    obligations: list[Obligation]
    approvals: list[Approval]
    contacts: list[Contact]
    deals: list[Deal]
    followUps: list[FollowUp]
    morningBrief: MorningBrief


def schema_document() -> dict:
    """The JSON Schema committed at docs/owner-snapshot-v1.schema.json —
    generated from the models above so the two cannot drift silently."""
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": SCHEMA_ID,
            **OwnerSnapshotV1.model_json_schema(by_alias=True)}


# ---------------------------------------------------------------------------
# Policy checks — refuse-before-the-artifact, like every other gate
# ---------------------------------------------------------------------------

def check_policy() -> None:
    """Refuse if any table names a denied source field or produces a
    forbidden output key. Runs on every build, so a mutated table is a
    refused export, not a leak."""
    for section, table in _tables().items():
        for src, dst, kind in table:
            if src in DENY_SOURCE_FIELDS:
                raise SnapshotPolicyError(
                    f"{section}: source field {src!r} is on the deny list")
            if FORBIDDEN_KEY_RE.search(dst):
                raise SnapshotPolicyError(
                    f"{section}: output key {dst!r} matches the forbidden pattern")
            if kind not in _KINDS:
                raise SnapshotPolicyError(f"{section}: unknown kind {kind!r}")
    for name in BRIEF_SECTIONS:
        if FORBIDDEN_KEY_RE.search(name):
            raise SnapshotPolicyError(
                f"brief section {name!r} matches the forbidden pattern")


def verify_document(document: Any, needles: Optional[tuple[str, ...]] = None,
                    _where: str = "$") -> None:
    """Walk the finished document: no key may match FORBIDDEN_KEY_RE and no
    string may carry a local path or the data directory."""
    needles = _place_needles() if needles is None else needles
    if isinstance(document, dict):
        for key, value in document.items():
            if FORBIDDEN_KEY_RE.search(str(key)):
                raise SnapshotPolicyError(f"forbidden key {key!r} at {_where}")
            verify_document(value, needles, f"{_where}.{key}")
    elif isinstance(document, list):
        for i, item in enumerate(document):
            verify_document(item, needles, f"{_where}[{i}]")
    elif isinstance(document, str):
        lowered = document.lower()
        for needle in needles:
            if needle and needle.lower() in lowered:
                raise SnapshotPolicyError(f"data directory leaked at {_where}")
        if (_DRIVE_PATH_RE.search(document) or _UNC_PATH_RE.search(document)
                or _HOME_PATH_RE.search(document)):
            raise SnapshotPolicyError(f"local path leaked at {_where}")


# ---------------------------------------------------------------------------
# Build + export
# ---------------------------------------------------------------------------

def _app_version() -> str:
    """Mirrors main.app_version(): the installer version stamped by the
    desktop supervisor, or "dev" when started from the venv."""
    return (os.environ.get("RIDIAN_APP_VERSION") or "").strip() or "dev"


def _local_utc_offset(now_local: _dt.datetime) -> str:
    raw = now_local.strftime("%z") or "+0000"
    return f"{raw[:3]}:{raw[3:]}"


def build_snapshot(version: Optional[str] = None,
                   now: Optional[_dt.datetime] = None) -> dict:
    """Assemble, validate against the contract, and policy-check the
    document. Pure with respect to the state store: reads only."""
    check_policy()
    c = _Coercer()
    now_utc = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    now_local = now_utc.astimezone()

    recent_work = _recent_work(c)
    projects = _projects(c)
    legacy_projects = _legacy_projects(c)
    obligations = _obligations(c)
    approvals = _approvals(c)
    contacts = _contacts(c)
    deals = _deals(c)
    follow_ups = _follow_ups(c)
    morning_brief = _morning_brief(c)

    document = {
        "schema": SCHEMA,
        "version": VERSION,
        "generatedAt": now_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": {
            "application": APPLICATION,
            "version": c.text(version if version is not None else _app_version()),
            "localUtcOffset": _local_utc_offset(now_local),
        },
        "summary": {
            "recentWork": len(recent_work),
            "projects": len(projects),
            "legacyProjects": len(legacy_projects),
            "obligations": len(obligations),
            "obligationsDue": sum(1 for o in obligations if o["due"] is not None),
            "approvalsPending": len(approvals),
            "approvalsStale": sum(1 for a in approvals if a["stale"]),
            "contacts": len(contacts),
            "deals": len(deals),
            "dealsActive": sum(1 for d in deals if d["active"]),
            "followUpsOpen": len(follow_ups),
        },
        "recentWork": recent_work,
        "projects": projects,
        "legacyProjects": legacy_projects,
        "obligations": obligations,
        "approvals": approvals,
        "contacts": contacts,
        "deals": deals,
        "followUps": follow_ups,
        "morningBrief": morning_brief,
    }
    # Contract first (shape), then policy (content). Both refuse-before-write.
    OwnerSnapshotV1.model_validate(document)
    verify_document(document)
    return document


def exports_dir() -> Path:
    return data_dir() / EXPORTS_DIRNAME


def export_snapshot(version: Optional[str] = None,
                    now: Optional[_dt.datetime] = None) -> dict:
    """Write <data_dir>/exports/owner-snapshot-<local stamp>.json atomically.
    Returns the path and the summary so the caller can show exactly what
    was written and where. Never uploads."""
    import json  # local import keeps the module's import surface minimal

    document = build_snapshot(version=version, now=now)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = exports_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{FILE_PREFIX}{stamp}.json"
    n = 0
    while final.exists():
        n += 1
        final = target_dir / f"{FILE_PREFIX}{stamp}-{n}.json"
    tmp = target_dir / f".{final.name}.tmp"
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, final)
    log.info("owner_snapshot.exported file=%s recent=%d approvals=%d",
             final.name, document["summary"]["recentWork"],
             document["summary"]["approvalsPending"])
    return {"path": str(final), "fileName": final.name,
            "generatedAt": document["generatedAt"],
            "summary": document["summary"]}
