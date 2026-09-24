"""Operator — natural-command → finished business artifacts.

v1.1: replaced the keyword intent recognizer + hardcoded audiobook pipeline
with a planner agent + tool registry. Any command in the same shape
("research X and make me an audiobook", "brief me on Y", "scan recent
developments in Z") is now routable without per-intent code.

Responsibilities:
    - Build a per-operation context (folder, mutable record, emit fn).
    - Hand the operator's natural-language command to the planner agent.
    - Stream the agent's tool calls + outputs as SSE events the renderer
      already knows how to render (step / artifact / error / complete).
    - Persist the operation log no matter what (success, partial, failed).

If a tool fails, the agent reports it honestly and stops — we do NOT
fabricate sources, scripts, or audio. This is the difference between an
operator and a prompt wrapper.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from ..agents import ALLOWED_EFFORT_LEVELS, ALLOWED_RESEARCH_MODELS, default_model
from ..agents.planner_agent import build_planner_system
from . import (gmail_service, google_drive_service, memory_service,
               operation_log_service, parked_runs, state_store)
from .anthropic_runtime import date_line, estimate_cost_usd, get_client
from .artifact_service import create_run_folder
from .operator_context import OperatorContext, set_current_operator
from .operator_tools import (
    CONTACT_ADMIN_CANCEL,
    CONTACT_ADMIN_PROCEED,
    INVOICE_CANCEL,
    INVOICE_PROCEED,
    ITEM_TOKEN_RE,
    RESTORE_CANCEL,
    RESTORE_PROCEED,
    absorb_stated_numbers,
    PLANNER_TOOLS,
    PROPOSAL_CANCEL,
    PROPOSAL_PROCEED,
    RESEARCH_PLAN_CANCEL,
    RESEARCH_PLAN_PROCEED,
    detect_deliverable_intent,
    detect_save_intent,
    detect_source_lock,
    extract_emails,
    extract_stated_numbers,
    SMS_CANCEL,
    SMS_PROCEED,
)
from .settings_service import (
    apply_to_environment,
    get_bool_setting,
    get_effective_value,
    load_settings,
)

log = logging.getLogger("ridian.operator")

EmitFn = Callable[[dict], Awaitable[None]]

# Per-operation safety rail. The planner prompt caps itself at 8 tool calls
# (10 drafts for contact sweeps) but the SDK also enforces max_turns, so a
# runaway agent can't loop forever. Sweeps need headroom: one model turn per
# draft plus research/receipt turns.
_MAX_PLANNER_TURNS = 24


# v2: resumable operations. A run that pauses on a needs_input keeps its
# OperatorContext (folder, mutable record, source-lock + grounding flags) AND
# the SDK conversation history in a session, so POST /operations/{id}/continue
# resumes the SAME operation with the user's answer as context — not a new
# isolated run. In-memory is fine: the desktop backend is single-user / process.
@dataclass
class _OperationSession:
    operator: OperatorContext
    folder: Path
    system: str          # the planner system prompt (tool list spliced in)
    input_list: list     # mirrored Anthropic messages — full conversation history
    upload_state_line: str


_SESSIONS: dict[str, _OperationSession] = {}

# v7.8 (0.9.18): closing the app. drain() sets _DRAINING; a run in flight
# stops at its next step boundary (the model turn and its tools done and
# mirrored) and is checkpointed; it resumes from there after the restart.
# _IN_FLIGHT holds the runs inside a planner turn right now.
_DRAINING = False
_IN_FLIGHT: set = set()
DRAIN_GRACE_SECONDS = 20.0
CHECKPOINTED = "checkpointed"
CLOSING_MESSAGE = ("Ridian Operator is closing — this run stopped after its current step "
                   "and continues from there when Ridian Operator starts again.")
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}

# v7.3 Ridian Jobs: in-process run lifecycle listeners. Pauses and endings
# are persisted (the operations store's save listener sees them); a run
# STARTING or RESUMING in-thread is not, so it is announced here. Listeners
# are fn(operation_id, phase, record) with phase "started" | "resumed";
# they must be quick and never raise (a failing listener is logged and
# ignored — it can never break a run).
_RUN_LISTENERS: list = []


def add_run_listener(fn) -> None:
    if fn not in _RUN_LISTENERS:
        _RUN_LISTENERS.append(fn)


def remove_run_listener(fn) -> None:
    try:
        _RUN_LISTENERS.remove(fn)
    except ValueError:
        pass


def _announce_run(record: dict, phase: str) -> None:
    for fn in list(_RUN_LISTENERS):
        try:
            fn(str(record.get("id") or ""), phase, record)
        except Exception:  # noqa: BLE001 — a listener never breaks a run
            log.warning("operator.run_listener_failed phase=%s", phase, exc_info=True)


# v7.3 Ridian Jobs: the ONLY origin stamp a run may carry. Set solely by
# jobs_service (never by a route: OperationRunRequest has no such field, so
# the desktop composer and the phone companion can never forge it).
JOB_SOURCE = "owner-workspace"


def _session_lock(operation_id: str) -> asyncio.Lock:
    lock = _SESSION_LOCKS.get(operation_id)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[operation_id] = lock
    return lock


def _drop_session(operation_id: str) -> None:
    _SESSIONS.pop(operation_id, None)
    _SESSION_LOCKS.pop(operation_id, None)
    parked_runs.delete(operation_id)   # v7.6: nothing can resume it now


def _slug_for_command(command: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", " ", command or "").strip()
    base = re.sub(r"\s+", "-", base).lower()
    return f"operator-{base[:60] or 'operation'}"


def _finalized_view(record: dict) -> dict:
    """Clean serializable snapshot for disk + the SSE 'complete' event."""
    return {
        "id": record["id"],
        "command": record["command"],
        "intent": record["intent"],
        "artifact_folder": record["artifact_folder"],
        "started_at": record["started_at"],
        "completed_at": record.get("completed_at") or datetime.now().isoformat(timespec="seconds"),
        "status": record["status"],
        "steps": record["steps"],
        "tools_used": sorted(set(record["tools_used"])),
        "sources_count": record["sources_count"],
        "audio_generated": record["audio_generated"],
        "audio_duration_seconds": record["audio_duration_seconds"],
        "artifacts": record["artifacts"],
        "errors": record["errors"],
        # v3.2: the run's dollar ledger — accumulated planner turns, sub-agent
        # calls, AND failed calls' partials, against the ceiling snapshotted
        # at intake. Persisted so every run's true cost survives in history.
        "spend_usd": round(float(record.get("spend_usd", 0.0) or 0.0), 4),
        "cost_ceiling_usd": record.get("cost_ceiling_usd"),
        # v7.0: texts this run sent — Twilio message SID, status, price. The
        # recipient number rides here (it is the operator's own allowlist);
        # the Owner Snapshot exporter denies this field, so it never travels.
        "sms_messages": list(record.get("sms_messages") or []),
        # v3.3: review-email fields. "command" above is the ORIGINAL initiating
        # request (set once at intake; a /continue never overwrites it — resume
        # answers only ride the start EVENT payload). These carry the research
        # self-audit and coverage so the email shows what a run cost and
        # covered without re-opening artifacts, plus the plan-approval flags
        # so "you approved it" comes from data, not inference.
        "reconciliation": record.get("reconciliation", ""),
        "source_titles": record.get("source_titles", []),
        "research_approved": bool(record.get("research_approved")),
        "research_declined": bool(record.get("research_declined")),
        # v1.2: memory proposals from this operation. Each carries its own
        # status ("proposed" | "committed" | "dismissed") so reloaded runs
        # don't re-prompt for items the operator already decided on.
        "proposed_memory_updates": record.get("proposed_memory_updates", []),
        # v1.7: missing-info questions + the planner's final receipt text.
        "needs_input": record.get("needs_input", []),
        "receipt": record.get("receipt", ""),
        # v2: paused-awaiting-user? The renderer routes the next answer to
        # POST /operations/{id}/continue (resume) instead of starting a new run.
        "awaiting_input": bool(record.get("awaiting_input")),
        # v2.8: project grouping (sidebar Projects section).
        "project_id": record.get("project_id", ""),
        # v3.6: background run — the renderer's notification registry keys
        # off this + status to badge done / needs-attention runs.
        "background": bool(record.get("background")),
        # v7.3 Ridian Jobs: where the command came from. "owner-workspace"
        # (with the site's job id) for a command the owner sent from the
        # Owner Workspace; "" for one typed on this PC or the phone.
        "source": str(record.get("source") or ""),
        "job_id": str(record.get("job_id") or ""),
    }


def _memory_context_snippet() -> str:
    """Compact, plain-text summary of current memory state for the planner.

    Read at the start of every operation so the planner can ground its plan
    in what Ridian already knows (and avoid re-proposing facts that are
    already on file). Kept short — the planner's main work is the operation,
    not summarizing memory.
    """
    parts: list[str] = []
    try:
        counts = memory_service.memory_summary()
        parts.append(
            "Memory snapshot — "
            f"{counts.get('contacts', 0)} contacts, "
            f"{counts.get('facts', 0)} facts, "
            f"{counts.get('open_follow_ups', 0)} open follow-ups, "
            f"{counts.get('decisions', 0)} decisions on file."
        )
    except Exception:
        parts.append("Memory snapshot unavailable.")

    # v1.6: the Operator Profile is the difference between generic output
    # and operator-specific moves. Inject it whole (it's small free text).
    # v5.1: TWO stores feed it — Settings identity (operator_name /
    # operator_email / company_name) and the Memory → Profile free text.
    # They are merged here; the EMPTY notice fires ONLY when BOTH are blank.
    # (The old code read only the memory store, so a filled Settings profile
    # still produced "Operator profile: EMPTY" in every receipt.)
    try:
        profile = memory_service.get_profile()
        filled = {k: v for k, v in profile.items() if (v or "").strip()}
        s = load_settings()
        identity = [
            (label, (s.get(key) or "").strip())
            for label, key in (("Operator", "operator_name"),
                               ("Email", "operator_email"),
                               ("Company", "company_name"))
            if (s.get(key) or "").strip()
        ]
        if filled or identity:
            labels = {
                "operator": "Who", "business": "Business", "offerings": "Sells",
                "customers": "Customers", "goal": "Quarter goal",
                "avoid": "Not interested in", "notes": "Notes",
            }
            lines = [f"  {label}: {v}" for label, v in identity]
            lines += [f"  {labels.get(k, k)}: {v.strip()}" for k, v in filled.items()]
            parts.append("Operator profile:\n" + "\n".join(lines))
        else:
            parts.append(
                "Operator profile: EMPTY. Results will be generic — suggest "
                "filling Memory → Profile in your receipt when relevant."
            )
    except Exception:
        pass

    try:
        brand = memory_service.get_brand() or {}
        voice_lines = []
        for k, label in (("ridian", "Ridian"), ("open_gulf", "Open Gulf"), ("buns", "Buns")):
            v = (brand.get(k, {}).get("voice") or "").strip()
            if v:
                voice_lines.append(f"  {label}: {v[:160]}")
        if voice_lines:
            parts.append("Brand voices:\n" + "\n".join(voice_lines))
        else:
            parts.append("Brand voices: none defined yet.")
    except Exception:
        pass

    # v1.6: full contact + follow-up details (not just counts) so the
    # contact-sweep recipe can actually personalize. Personal-scale data —
    # cap at 20 contacts / 15 follow-ups to keep the prompt bounded.
    try:
        contacts = memory_service.list_contacts()[:20]
        if contacts:
            lines = []
            for c in contacts:
                bits = [c.get("name", "")]
                if c.get("role"):    bits.append(c["role"])
                if c.get("company"): bits.append(c["company"])
                if c.get("email"):   bits.append(c["email"])
                if c.get("last_contact_iso"): bits.append(f"last contact {c['last_contact_iso']}")
                if c.get("notes"):   bits.append(f"notes: {c['notes'][:120]}")
                lines.append("  - " + " | ".join(b for b in bits if b))
            parts.append("Contacts on file:\n" + "\n".join(lines))
    except Exception:
        pass

    try:
        fups = memory_service.list_open_follow_ups()[:15]
        if fups:
            lines = []
            for f in fups:
                bits = [f.get("what", "")]
                if f.get("who"):     bits.append(f"who: {f['who']}")
                if f.get("due_iso"): bits.append(f"due: {f['due_iso']}")
                lines.append("  - " + " | ".join(b for b in bits if b))
            parts.append("Open follow-ups:\n" + "\n".join(lines))
    except Exception:
        pass

    # A few recent operations help the planner avoid re-doing yesterday's brief.
    try:
        recent = operation_log_service.list_recent(limit=3)
        if recent:
            lines = []
            for op in recent:
                lines.append(
                    f"  - {op.get('completed_at', '?')} [{op.get('status', '?')}]"
                    f" {op.get('command', '')[:90]}"
                )
            parts.append("Last few operations:\n" + "\n".join(lines))

            # v1.5 conversational follow-up: when the most recent op completed
            # within the last 5 minutes, include its artifact list + folder so
            # the planner can answer follow-up commands like "now draft an
            # email about it" without losing context. Outside the 5-minute
            # window the user is starting a new train of thought, so we don't
            # bias the planner.
            latest = recent[0]
            completed_iso = latest.get("completed_at", "")
            if completed_iso:
                try:
                    completed = datetime.fromisoformat(completed_iso.replace("Z", ""))
                    if datetime.now() - completed < timedelta(minutes=5):
                        artifact_names = [
                            a.get("name", "")
                            for a in latest.get("artifacts", [])
                            if a.get("name")
                        ]
                        parts.append(
                            "Most recent operation (just completed; treat as the "
                            "context for any follow-up):\n"
                            f"  Command: {latest.get('command', '')}\n"
                            f"  Folder:  {latest.get('artifact_folder', '')}\n"
                            f"  Artifacts: {', '.join(artifact_names) or '(none)'}\n"
                            "  If this run's command uses 'it', 'that', 'them', "
                            "'the brief', 'the script', etc., assume the user is "
                            "referring to the run above. Do not re-research the "
                            "same topic — read the artifacts in the folder above "
                            "with write_file (or skip straight to draft_gmail / "
                            "auto_upload_drive as appropriate)."
                        )
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    return "\n".join(parts)


async def _surface_planner_message(operator: OperatorContext, message) -> None:
    """Surface one planner turn to the renderer.

    Tools emit their own step/artifact SSE events from inside their bodies, so
    this only handles the planner's *meta* output: the tool-call markers the
    renderer may show, and the text — every turn's text goes out as a
    'message' event and the last non-empty one is captured as the receipt (so
    receipt-only runs count as completed, and the receipt survives reloads).
    """
    for block in message.content:
        if block.type == "tool_use":
            await operator.emit({
                "event": "message",
                "data": {"text": f"Planner → calling tool: {block.name}"},
            })
    text = "".join(b.text for b in message.content if b.type == "text").strip()
    if text:
        operator.record["receipt"] = text
        await operator.emit({"event": "message", "data": {"text": text}})


async def _emit_start(emit: EmitFn, record: dict, command: str, folder: Path) -> None:
    await emit({"event": "start", "data": {
        "id": record["id"],
        "command": command,
        "intent": record["intent"],
        "artifact_folder": str(folder),
        "started_at": record["started_at"],
    }})


async def _persist_and_complete(emit: EmitFn, record: dict, folder: Path) -> dict:
    """Decide final status, snapshot to disk, append to operations log, emit."""
    # Decide status from what the tools actually produced.
    has_any_artifact = bool(record["artifacts"])
    has_errors = bool(record["errors"])
    # v2: "partial" means still waiting on the user RIGHT NOW (awaiting_input),
    # not merely that the run ever asked a question — answered questions stay in
    # record["needs_input"] for history but must not force a completed resume to
    # read as partial.
    has_needs = bool(record.get("awaiting_input"))
    has_receipt = bool((record.get("receipt") or "").strip())

    if has_errors and not has_any_artifact:
        record["status"] = "failed"
    elif has_errors or has_needs:
        # Waiting-on-the-user is incomplete by definition, not a failure.
        record["status"] = "partial"
    elif has_any_artifact or has_receipt:
        # Receipt-only runs are legitimate: questions answered from memory
        # produce no artifacts but ARE completed work.
        record["status"] = "completed"
    else:
        record["status"] = "failed"

    operation_log_service.finalize(record, status=record["status"])

    snapshot = _finalized_view(record)
    try:
        # v7.8: the run's own log is bookkeeping, never one of its files —
        # it is written here but no longer listed as an artifact.
        (folder / "operation_log.json").write_text(
            json.dumps(snapshot, indent=2) + "\n", encoding="utf-8",
        )
    except OSError:
        pass

    operation_log_service.upsert_operation(snapshot)
    await emit({"event": "complete", "data": snapshot})
    return snapshot


def _compute_upload_state() -> str:
    """The auto-upload line the planner reads verbatim (Drive on/off + connected)."""
    auto_upload_on = get_bool_setting("operator_auto_upload_drive", default=True)
    try:
        drive_connected = bool(google_drive_service.get_status().get("connected"))
    except Exception:  # noqa: BLE001
        drive_connected = False
    if auto_upload_on and drive_connected:
        return "Drive auto-upload: on (Google Drive connected). Call auto_upload_drive after artifacts."
    if auto_upload_on and not drive_connected:
        return "Drive auto-upload: on, BUT Google Drive is not connected. Skip auto_upload_drive and mention in your summary."
    return "Drive auto-upload: off (user disabled). Skip auto_upload_drive."


def _build_planner_input(command: str, upload_state_line: str) -> str:
    # date_line() reads the live clock per run — "this week" / "latest" in
    # commands must resolve against today, not the model's training era.
    return (
        f"{date_line()}\n\n"
        f"Operator command:\n{command}\n\n"
        f"Current memory + recent operations:\n{_memory_context_snippet()}\n\n"
        f"Auto-upload state: {upload_state_line}\n\n"
        "If the command is conversational (a greeting, an opinion question, "
        "small talk) or fully answerable from the memory context above, call "
        "NO tools — just answer directly and briefly in your receipt. "
        "Otherwise plan the minimum-viable sequence of tool calls from your "
        "registry, execute them, verify each result, and then give a short "
        "final receipt of what landed on disk. Do not invent tools.\n\n"
        "After the artifact tools finish (and only if the operation produced "
        "real artifacts), you MAY call propose_memory_update for up to three "
        "items the user would want Ridian to remember from this run — facts, "
        "contacts, follow-ups, or decisions that surfaced organically. Do not "
        "propose anything that was already in memory above. Do not invent."
    )


async def _run_turn(session: _OperationSession, messages: list) -> None:
    """Run ONE planner turn on the Anthropic tool runner.

    The runner drives the model → tool → result loop; our tools emit their own
    step/artifact SSE from inside their bodies (they read the OperatorContext
    off the contextvar bound here). We mirror the conversation into
    ``session.input_list`` as it grows — the runner keeps its own private
    copy — so a /continue can resume the SAME operation with full history.
    ``pause_turn`` (a long server-tool turn parking itself) is resumed by
    restarting the runner with the paused assistant turn appended, capped.
    """
    set_current_operator(session.operator)
    client = get_client()
    restarts = 0
    turn_no = 0
    turn_started = time.monotonic()
    while True:
        runner = client.beta.messages.tool_runner(
            model=default_model(),
            max_tokens=16000,
            system=session.system,
            tools=PLANNER_TOOLS,
            messages=messages,
            max_iterations=_MAX_PLANNER_TURNS,
            # EXPERIMENT (A/B vs thinking-off): adaptive thinking on the gate
            # brain — Opus 4.8 omits = off, so this is the deliberate ON arm.
            # Thinking blocks enter the mirrored history and replay on resume
            # unchanged (same model), which the resume leg of the A/B verifies.
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
        )
        last = None
        async for message in runner:
            last = message
            turn_no += 1
            # Per-turn forensics (model/thinking experiments): ms is this
            # turn's API latency — the clock restarts after tools execute, so
            # tool time is excluded. output_tokens includes thinking tokens.
            u = getattr(message, "usage", None)
            log.info(
                "planner.turn n=%d ms=%d in=%d out=%d stop=%s",
                turn_no, int((time.monotonic() - turn_started) * 1000),
                int(getattr(u, "input_tokens", 0) or 0),
                int(getattr(u, "output_tokens", 0) or 0),
                getattr(message, "stop_reason", ""),
            )
            # Mirror history: the assistant turn, then any tool results the
            # runner produced for it (cached — tools still execute once).
            messages.append({"role": "assistant", "content": message.content})
            tool_response = await runner.generate_tool_call_response()
            if tool_response is not None:
                messages.append(tool_response)
            await _surface_planner_message(session.operator, message)
            turn_started = time.monotonic()
            # v3.2: the run's dollar fence covers planner turns too. Checked
            # AFTER mirroring so the history stays consistent for a /continue.
            if await _absorb_planner_spend(session.operator, message):
                session.input_list = messages
                return None
            # v7.8: the app is closing. This step — the model turn and the
            # tools it called — is done and mirrored: stop here, at a clean
            # boundary, before the next model call. (A turn that ended with
            # no tool call is the run's last; it finishes normally.)
            if _DRAINING and tool_response is not None:
                session.input_list = messages
                return CHECKPOINTED
        if last is None or last.stop_reason != "pause_turn" or restarts >= 3:
            break
        if _DRAINING:
            session.input_list = messages
            return CHECKPOINTED
        restarts += 1

    session.input_list = messages
    return None


async def _persist_or_pause(emit: EmitFn, record: dict, folder: Path) -> dict:
    """If the turn ended paused-awaiting-the-user, snapshot as 'awaiting_input'
    and KEEP the session for a /continue. Otherwise finalize and drop it."""
    if record.get("awaiting_input"):
        record["status"] = "awaiting_input"
        # v7.6: the session also goes to disk BEFORE anything announces the
        # park, so an answer after a restart can rebuild it (parked_runs).
        session = _SESSIONS.get(record["id"])
        if session is not None:
            parked_runs.save(session)
        snapshot = _finalized_view(record)
        try:
            (folder / "operation_log.json").write_text(
                json.dumps(snapshot, indent=2) + "\n", encoding="utf-8",
            )
        except OSError:
            pass
        operation_log_service.upsert_operation(snapshot)
        # v6.9.7: a run parking on a question is one of the three notifiable
        # moments. Fire-and-forget; the pause itself never depends on it.
        try:
            from . import push_service
            push_service.notify_run_parked(snapshot)
        except Exception:  # noqa: BLE001 — notification is never load-bearing
            log.warning("operator.push_notify_failed", exc_info=True)
        await emit({"event": "complete", "data": snapshot})
        return snapshot
    snapshot = await _persist_and_complete(emit, record, folder)
    _drop_session(record["id"])
    return snapshot


# ---------------------------------------------------------------------------
# Source grounding from pasted text / uploaded PDFs (same provenance as read_url)
# ---------------------------------------------------------------------------

def _ground_with_text(operator: OperatorContext, text: str, label: str) -> None:
    """Write source text into the run's source.md and mark the run grounded —
    the same verified provenance a successful read_url provides."""
    src = operator.folder / "source.md"
    prior = src.read_text(encoding="utf-8") if src.exists() else "# Fetched sources\n\n"
    src.write_text(prior + f"## {label}\n\n{(text or '').strip()}\n\n---\n\n", encoding="utf-8")
    operator.record["grounding_ok"] = True


# Staged source (Flow B): the operator attaches a PDF / pastes text BEFORE
# giving a command, and the next operation is grounded strictly in it. A single
# global slot — the desktop backend is single-user / single-process.
_STAGED_SOURCE: "dict | None" = None


def stage_source(text: str, label: str) -> dict:
    """Stage grounding source text for the NEXT operation. Raises ValueError if
    the text is too thin to be a real source."""
    global _STAGED_SOURCE
    t = (text or "").strip()
    if len(t) < 40:
        raise ValueError("That source text is too short to ground a run.")
    _STAGED_SOURCE = {"text": t, "label": label or "Attached source", "chars": len(t)}
    log.info("source.staged label=%s chars=%d", _STAGED_SOURCE["label"], len(t))
    return {"label": _STAGED_SOURCE["label"], "chars": _STAGED_SOURCE["chars"]}


def staged_source() -> "dict | None":
    return ({"label": _STAGED_SOURCE["label"], "chars": _STAGED_SOURCE["chars"]}
            if _STAGED_SOURCE else None)


def clear_staged_source() -> None:
    global _STAGED_SOURCE
    _STAGED_SOURCE = None


def save_source_pdf(operation_id: str, data: bytes, filename: str = "source.pdf") -> None:
    """Persist an uploaded PDF into the operation's run folder (git-ignored via
    outputs/*/) so the raw source rides along with source.md."""
    session = _SESSIONS.get(operation_id) or _restore_session(operation_id)[0]
    if not session:
        return
    try:
        (session.folder / "source.pdf").write_bytes(data or b"")
    except OSError:
        pass


def _consume_staged_source(operator: OperatorContext) -> str:
    """If a source is staged, ground the run in it and return a planner note+text
    to prepend to the command. Clears the staged source. "" when none.

    v2.5: only consumed when the run actually asked for a deliverable — a
    staged PDF must not glue itself to small talk ("How do you feel?") and burn
    tokens. Without intent it STAYS staged (the chip persists) for the next
    real build command."""
    global _STAGED_SOURCE
    if not _STAGED_SOURCE:
        return ""
    if not operator.record.get("deliverable_intent", True):
        log.info("source.staged_held reason=no_deliverable_intent")
        return ""
    staged = _STAGED_SOURCE
    _STAGED_SOURCE = None
    _ground_with_text(operator, staged["text"], staged["label"])
    # Lock the run to the attached source so the build tools require this
    # grounding (already satisfied) and never silently fall back.
    operator.record["source_locked_url"] = (
        operator.record.get("source_locked_url") or f"attached:{staged['label']}"
    )
    return (
        "GROUNDING SOURCE — the operator attached this. Build the deliverables "
        "STRICTLY from the text below; do NOT use general knowledge or web search, "
        f"and omit anything not present here.\n\n{staged['text']}\n\n---\n"
    )


# Resume interpretation for a source-locked run whose grounding failed: a short
# "do general research" answer lifts the lock; a long paste is treated as the
# source text itself.
_GENERAL_RESEARCH_RE = re.compile(
    r"\b(general|web ?search|web research|go ahead|proceed|without (the )?source|"
    r"option a|anyway|just search)\b", re.IGNORECASE,
)


def _apply_grounding_answer(operator: OperatorContext, answer: str) -> str:
    """Relax the source-lock gate based on the operator's resume answer.

    Returns a note to prepend to the planner input, or "" if nothing to do.
    """
    rec = operator.record
    if (not rec.get("source_locked_url")
            or rec.get("grounding_ok") or rec.get("grounding_override")):
        return ""
    a = (answer or "").strip()
    # (b) The operator pasted the page text — treat it as the source itself.
    if len(a) >= 120:
        try:
            _ground_with_text(operator, a, "Operator-pasted source")
            return ("The operator pasted the source text; it is saved to source.md. "
                    "Build the deliverables STRICTLY from that text.")
        except OSError:
            return ""
    # (a) The operator authorized general web research — lift the lock.
    if _GENERAL_RESEARCH_RE.search(a):
        rec["grounding_override"] = True
        return ("The operator authorized GENERAL web research; the source lock is "
                "lifted for this run. Proceed with web_research and build the "
                "deliverables, and note in your receipt that they are NOT grounded "
                "in the originally requested source.")
    return ""  # e.g. the operator supplied a different URL — the planner handles it


# Plain-language equivalents of the plan buttons — matched at the START of the
# answer so "proceed, but keep it short" approves while an unrelated sentence
# that merely contains "go" does not.
_RESEARCH_APPROVE_RE = re.compile(
    r"^\s*(proceed|yes|yep|go(\s+ahead)?|approved?|run\s+it|do\s+it|ok(ay)?)\b", re.I)
_RESEARCH_DECLINE_RE = re.compile(
    r"^\s*(cancel|no\b|nope|stop|don'?t|abort|skip)", re.I)


def _apply_research_answer(operator: OperatorContext, answer: str) -> str:
    """Resolve a pending research-plan approval from the operator's resume
    answer. This is the ONLY writer of record["research_approved"] /
    ["research_declined"], so approval always comes from the operator's own
    words — the planner cannot set it (operator_tools._research_plan_gate
    checks the flags in code).

    Returns a note to prepend to the planner input, or "" if nothing to do.
    """
    rec = operator.record
    if (not rec.get("research_plan_asked")
            or rec.get("research_approved") or rec.get("research_declined")):
        return ""
    a = (answer or "").strip()
    if a == RESEARCH_PLAN_PROCEED or _RESEARCH_APPROVE_RE.match(a):
        rec["research_approved"] = True
        return ("The operator APPROVED the research plan. Call the research tool "
                "again now and complete the deliverable.")
    if a == RESEARCH_PLAN_CANCEL or _RESEARCH_DECLINE_RE.match(a):
        rec["research_declined"] = True
        return ("The operator DECLINED the research plan. Do not run any web "
                "research; acknowledge the cancellation briefly in your receipt.")
    # Unrecognized answer (a question, extra context): clear the asked flag so
    # the gate re-presents the plan on the next research call, and let the
    # planner respond to what the operator actually said.
    rec["research_plan_asked"] = False
    return ""


# ---------------------------------------------------------------------------
# Hard per-run cost ceiling (v3.2) — the dollar fence around the WHOLE run
# ---------------------------------------------------------------------------
# The whole operation is fenced, planner turns included — the planner's tokens
# are real money and a ceiling that excluded them wouldn't be honest. Blank /
# absent resolves to the DEFAULT so the fence is on out of the box (an
# untouched Settings field is blank — "blank = default" is the only way the
# default can ever apply); the deliberate no-ceiling switch is typing "off".

_DEFAULT_COST_CEILING_USD = 1.00
_CEILING_OFF_VALUES = frozenset({"off", "none", "no ceiling", "unlimited"})


def resolve_cost_ceiling() -> float | None:
    """The operator's per-run dollar ceiling from Settings: a float, or None
    for no ceiling. Unparseable input falls back to the DEFAULT — the safe
    failure direction for a spend fence is fenced, never silently open."""
    raw = (load_settings().get("operator_run_cost_ceiling_usd") or "").strip().lower()
    if not raw:
        return _DEFAULT_COST_CEILING_USD
    if raw in _CEILING_OFF_VALUES:
        return None
    try:
        value = float(raw.lstrip("$"))
    except ValueError:
        log.warning("cost_ceiling.unparseable value=%r — using the $%.2f default",
                    raw, _DEFAULT_COST_CEILING_USD)
        return _DEFAULT_COST_CEILING_USD
    if value <= 0:
        return None   # an explicit zero reads as "no fence", same as "off"
    return round(value, 2)


_DEFAULT_MONTHLY_BUDGET_USD = 50.00


def resolve_monthly_budget() -> float | None:
    """The operator's MONTHLY spend budget from Settings: a float, or None
    for none set. Same parsing contract as resolve_cost_ceiling — blank is
    the default (budgeted out of the box), "off" is the deliberate switch,
    junk falls back to the default."""
    raw = (load_settings().get("operator_monthly_budget_usd") or "").strip().lower()
    if not raw:
        return _DEFAULT_MONTHLY_BUDGET_USD
    if raw in _CEILING_OFF_VALUES:
        return None
    try:
        value = float(raw.lstrip("$"))
    except ValueError:
        log.warning("monthly_budget.unparseable value=%r — using the $%.2f default",
                    raw, _DEFAULT_MONTHLY_BUDGET_USD)
        return _DEFAULT_MONTHLY_BUDGET_USD
    if value <= 0:
        return None
    return round(value, 2)


def _ceiling_stop_message(run_spend: float, ceiling: float,
                          month_spent: float, monthly_cap: float | None) -> str:
    """The ceiling-stop message, built from ACTUAL budget state (pure —
    pinned by tests). The old text suggested raising the per-run ceiling
    'or setting it to off' unconditionally, which was dishonest whenever
    the monthly budget was the real constraint: a raised ceiling would
    just hit the monthly wall at higher cost. Rules:
      - always report this run's spend, the per-run limit, month-to-date
        against the monthly budget, and what remains;
      - suggest raising the per-run ceiling ONLY when the remaining
        monthly budget can actually absorb more than the ceiling;
      - when the monthly budget is binding or exhausted, say so plainly
        and never suggest raising or disabling the per-run ceiling."""
    head = (f"Run stopped at the per-run cost ceiling — ≈${run_spend:.2f} "
            f"spent of the ${ceiling:.2f} per-run limit.")
    if monthly_cap is None:
        return (head + " No monthly budget is set. If this run should go "
                "further, raise the per-run ceiling in Settings and run again.")
    remaining = max(0.0, round(monthly_cap - month_spent, 4))
    state = (f" Month-to-date: ${month_spent:.2f} of the ${monthly_cap:.2f} "
             f"monthly budget — ${remaining:.2f} remaining.")
    if remaining <= 0.005:
        return (head + state + " The monthly budget is exhausted, so raising "
                "the per-run ceiling would not help — the next run stops at "
                "the monthly wall instead. Wait for the new month, or revisit "
                "the monthly budget in Settings if it no longer reflects what "
                "you want to spend.")
    if remaining <= ceiling:
        return (head + state + " The monthly budget is the binding constraint "
                "here, not the per-run ceiling: less than one run's ceiling "
                f"remains. Leave the ceiling as it is — whatever runs next "
                f"this month has ${remaining:.2f} to work with.")
    return (head + state + " The monthly budget can absorb a longer run: "
            f"raise the per-run ceiling in Settings (up to ${remaining:.2f} "
            "remains this month) and run again.")


async def _absorb_planner_spend(operator: OperatorContext, message) -> bool:
    """Fold one planner turn's token cost into the run's spend and enforce the
    ceiling at the turn boundary. Returns True when the run must STOP.

    The billable tools gate themselves (operator_tools._cost_ceiling_gate +
    the runtime's live guard), so this trips only when planner turns alone
    push the run over the fence — the runaway-loop case. A turn that just
    ENDED (end_turn with nothing pending) is let through: stopping then would
    burn the receipt the money already paid for.
    """
    rec = operator.record
    u = getattr(message, "usage", None)
    turn_cost = estimate_cost_usd(
        default_model(),
        int(getattr(u, "input_tokens", 0) or 0),
        int(getattr(u, "output_tokens", 0) or 0),
    )
    rec["spend_usd"] = round(
        float(rec.get("spend_usd", 0.0) or 0.0) + turn_cost, 4)
    ceiling = rec.get("cost_ceiling_usd")
    if ceiling is None or rec["spend_usd"] <= ceiling:
        return False
    if getattr(message, "stop_reason", "") == "end_turn":
        return False
    # Month-to-date INCLUDES this run: it isn't in the log yet (the record
    # is finalized after the run ends), so add its spend explicitly.
    month_spent = round(
        operation_log_service.month_to_date_spend() + rec["spend_usd"], 4)
    msg = _ceiling_stop_message(rec["spend_usd"], ceiling, month_spent,
                                resolve_monthly_budget())
    rec["errors"].append(msg)
    await operator.emit_step(name="cost_ceiling", status="failed", detail=msg)
    await operator.emit_error(msg)
    return True


def _apply_invoice_answer(operator: OperatorContext, answer: str) -> str:
    """Resolve a pending QuickBooks invoice preview from the operator's
    resume answer — the ONLY writer of record["invoice_approved"] /
    ["invoice_declined"] (operator_tools._invoice_approval_gate checks the
    flags plus a payload signature in code; the planner can't set them)."""
    rec = operator.record
    if (not rec.get("invoice_plan_asked")
            or rec.get("invoice_approved") or rec.get("invoice_declined")):
        return ""
    a = (answer or "").strip()
    if a == INVOICE_PROCEED or _RESEARCH_APPROVE_RE.match(a):
        rec["invoice_approved"] = True
        return ("The operator APPROVED the previewed invoice. Call "
                "create_quickbooks_invoice again with the SAME arguments.")
    if a == INVOICE_CANCEL or _RESEARCH_DECLINE_RE.match(a):
        rec["invoice_declined"] = True
        return ("The operator DECLINED the invoice. Do not create it; "
                "acknowledge briefly in your receipt.")
    rec["invoice_plan_asked"] = False   # unrecognized → re-present on next call
    return ""


def _apply_proposal_answer(operator: OperatorContext, answer: str) -> str:
    """Resolve a pending proposal preview from the operator's resume answer —
    the ONLY writer of record["proposal_doc_approved"] / ["proposal_doc_declined"]
    (operator_tools._proposal_approval_gate checks the flags plus a payload
    signature in code; the planner can't set them). Mirrors the invoice gate."""
    rec = operator.record
    if (not rec.get("proposal_doc_asked")
            or rec.get("proposal_doc_approved") or rec.get("proposal_doc_declined")):
        return ""
    a = (answer or "").strip()
    if a == PROPOSAL_PROCEED or _RESEARCH_APPROVE_RE.match(a):
        rec["proposal_doc_approved"] = True
        return ("The operator APPROVED the previewed proposal. Call "
                "draft_proposal again with the SAME arguments.")
    if a == PROPOSAL_CANCEL or _RESEARCH_DECLINE_RE.match(a):
        rec["proposal_doc_declined"] = True
        return ("The operator DECLINED the proposal. Do not write it; "
                "acknowledge briefly in your receipt.")
    rec["proposal_doc_asked"] = False   # unrecognized → re-present on next call
    return ""


def _apply_sms_answer(operator: OperatorContext, answer: str) -> str:
    """Resolve a pending text-message preview from the operator's answer —
    the ONLY writer of record["sms_approved"] / ["sms_declined"]
    (operator_tools._sms_approval_gate checks the flags plus a
    label+number+body signature in code; the planner can't set them).
    Mirrors the invoice gate."""
    rec = operator.record
    if (not rec.get("sms_send_asked")
            or rec.get("sms_approved") or rec.get("sms_declined")):
        return ""
    a = (answer or "").strip()
    if a == SMS_PROCEED or _RESEARCH_APPROVE_RE.match(a):
        rec["sms_approved"] = True
        return ("The operator APPROVED the previewed text message. Call "
                "send_sms again with the SAME arguments.")
    if a == SMS_CANCEL or _RESEARCH_DECLINE_RE.match(a):
        rec["sms_declined"] = True
        return ("The operator DECLINED the text message. Do not send it; "
                "acknowledge briefly in your receipt.")
    rec["sms_send_asked"] = False   # unrecognized → re-present on next call
    return ""


def _apply_contact_admin_answer(operator: OperatorContext, answer: str) -> str:
    """Resolve a pending contact merge/delete preview from the operator's
    resume answer — the ONLY writer of record["contact_admin_approved"] /
    ["contact_admin_declined"] (operator_tools._contact_admin_gate checks
    the flags plus a payload signature in code; the planner can't set
    them). Mirrors the invoice gate."""
    rec = operator.record
    if (not rec.get("contact_admin_asked")
            or rec.get("contact_admin_approved") or rec.get("contact_admin_declined")):
        return ""
    a = (answer or "").strip()
    if a == CONTACT_ADMIN_PROCEED or _RESEARCH_APPROVE_RE.match(a):
        rec["contact_admin_approved"] = True
        return ("The operator APPROVED the previewed contact change. Call the "
                "same tool again with the SAME arguments.")
    if a == CONTACT_ADMIN_CANCEL or _RESEARCH_DECLINE_RE.match(a):
        rec["contact_admin_declined"] = True
        return ("The operator DECLINED the contact change. Do not apply it; "
                "acknowledge briefly in your receipt.")
    rec["contact_admin_asked"] = False   # unrecognized → re-present on next call
    return ""


def _apply_item_selection_answer(operator: OperatorContext, answer: str) -> str:
    """v6.7: consume [[qbo-item:ID]] tokens a catalog BUTTON prefilled into
    the answer — the SOLE writer of record["selected_catalog_items"], same
    trust model as the invoice/proposal appliers. Each id is validated
    against record["offered_catalog_items"] (the snapshot taken when the
    ask was emitted): an id that was never offered is REFUSED — recorded
    nowhere, and the planner is told so. Provenance is therefore
    "user selected catalog item id N", set and checked by code; display
    text plays no part."""
    rec = operator.record
    tokens = ITEM_TOKEN_RE.findall(answer or "")
    if not tokens:
        return ""
    offered = rec.get("offered_catalog_items") or {}
    accepted, refused = [], []
    for item_id in tokens:
        item = offered.get(str(item_id))
        if item is None:
            refused.append(str(item_id))
            continue
        rec.setdefault("selected_catalog_items", {})[str(item_id)] = dict(item)
        accepted.append(f"id {item_id} ({item['name']}, ${item['unit_price']})")
    notes = []
    if accepted:
        notes.append(
            "The operator SELECTED catalog item " + "; ".join(accepted) + ". "
            "Use that item_id (not item_name) for the line. The quantity "
            "must still come from the operator's own words.")
    if refused:
        notes.append(
            "REFUSED: item id(s) " + ", ".join(refused) + " were never "
            "offered in this run — they were NOT recorded. Ask the operator "
            "to pick from the buttons.")
    return "\n".join(notes)


def dismiss_operation(operation_id: str) -> dict:
    """v6.7: the operator explicitly CANCELS a pending (awaiting-input)
    task. Drops the live session so nothing can resume it, and marks the
    persisted record cancelled so history and the morning brief stop
    reporting it as waiting. v6.9.3: staged approvals owned by the run are
    VOIDED in the same breath — the earlier behavior (leaving them
    "independently answerable") left a cancelled run's $500 invoice
    approval live in the inbox for five days."""
    _drop_session(operation_id)
    ops = state_store.load_list("operations")
    changed = False
    for op in ops:
        if op.get("id") == operation_id and op.get("status") == "awaiting_input":
            op["status"] = "cancelled"
            op["awaiting_input"] = False
            op.setdefault("steps", []).append({
                "name": "cancelled", "status": "completed",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "completed_at": datetime.now().isoformat(timespec="seconds"),
                "detail": "Cancelled by the operator from the pending-task strip."})
            changed = True
    if changed:
        state_store.save("operations", ops)
        from .approval_inbox_service import void_for_operation  # lazy: cycle
        void_for_operation(operation_id, "owning run cancelled by the operator")
        _withdraw_pushes(operation_id, "cancelled")
    return {"cancelled": changed, "operation_id": operation_id}


def _withdraw_pushes(operation_id: str, reason: str) -> None:
    """v7.7: the phone notifications a run raised close once it is answered
    or cancelled (an expiry's own push closes them). Never load-bearing."""
    try:
        from . import push_service
        push_service.withdraw_run(operation_id, reason)
    except Exception:  # noqa: BLE001 — notification is never load-bearing
        log.warning("operator.push_withdraw_failed", exc_info=True)


# v7.6: why a parked run could not continue. The owner reads these (the
# thread, the notification, the site's replyText), so they say what happened.
EXPIRED_REASONS = {
    "state_missing": "Ridian Operator restarted and this run's saved state was not found.",
    "state_unreadable": "This run's saved state could not be read.",
    "interrupted": ("Ridian Operator closed while this run was continuing after your "
                    "answer, so it cannot pick up from the middle."),
    # v7.8: closed in the middle of a step (the drain's grace ran out, or
    # the app was killed): the step's outcome is unknown.
    "mid_step": ("Ridian Operator closed in the middle of one of this run's steps, "
                 "so it cannot pick up from the middle."),
}


def expired_message(why: str) -> str:
    reason = EXPIRED_REASONS.get(why, EXPIRED_REASONS["state_missing"])
    return (f"This run expired and cannot continue. {reason} "
            "Send the command again if it is still needed.")


async def _discard_event(_event: dict) -> None:
    return None


def _usable_parked(operation_id: str) -> "tuple[dict | None, str]":
    """(payload, "") for a run that can continue from its parked file, else
    (None, why) — the file is missing, unreadable, or was left mid-resume."""
    payload = parked_runs.load(operation_id)
    if payload is None:
        return None, ("state_unreadable" if parked_runs.exists(operation_id)
                      else "state_missing")
    if payload["state"] != parked_runs.PARKED:
        return None, "interrupted"
    return payload, ""


def _restore_session(operation_id: str) -> "tuple[_OperationSession | None, str]":
    """v7.6: rebuild a parked run's live session from its parked file.
    Returns (session, "") or (None, why). Synchronous on purpose: nothing
    awaits between the check and the registration, so two answers arriving
    together cannot both restore."""
    existing = _SESSIONS.get(operation_id)
    if existing is not None:
        return existing, ""
    op = next((o for o in state_store.load_list("operations")
               if isinstance(o, dict) and o.get("id") == operation_id), None)
    if op is None:
        return None, "unknown"
    if op.get("status") != "awaiting_input":
        return None, "ended"
    payload, why = _usable_parked(operation_id)
    if payload is None:
        return None, why
    session = _session_from_payload(payload)
    _SESSIONS[operation_id] = session
    log.info("operation.restored id=%s", operation_id)
    return session, ""


def _session_from_payload(payload: dict) -> "_OperationSession":
    folder = Path(payload["folder"])
    operator = OperatorContext(
        folder=folder, record=payload["record"], emit=_discard_event,
        sources_packet_text=str(payload.get("sources_packet_text") or ""),
        script_text=str(payload.get("script_text") or ""))
    return _OperationSession(
        operator=operator, folder=folder, system=payload["system"],
        input_list=payload["input_list"],
        upload_state_line=str(payload.get("upload_state_line") or ""))


def _mark_expired(op: dict, why: str, message: str, now: str) -> None:
    op["status"] = "failed"
    op["awaiting_input"] = False
    op.setdefault("errors", []).append(message)
    op["expired"] = {"reason": why, "message": message, "at": now}
    op.setdefault("steps", []).append({
        "name": "expired", "status": "failed",
        "started_at": now, "completed_at": now, "detail": message})


def expire_run(operation_id: str, why: str, record: "dict | None" = None) -> dict:
    """v7.6: a parked run that truly cannot continue must not stay waiting.
    Marks it failed with the reason — the jobs engine reports that to the
    site (awaiting_input / awaiting_approval -> failed) — voids its staged
    approvals, rewrites its run-folder log so a reopened thread shows the
    ending, and notifies once. A run that already ended is left alone.
    Returns what the window shows: the run, its command and a message."""
    _drop_session(operation_id)
    ops = state_store.load_list("operations")
    op = next((o for o in ops if isinstance(o, dict) and o.get("id") == operation_id), None)
    created = False
    if op is None and isinstance(record, dict) and record.get("id") == operation_id:
        # v7.8: closed mid-step before it ever parked — the record comes from
        # its "running" file, so the run is on record, honestly ended.
        try:
            op = _finalized_view(record)
        except (KeyError, TypeError, ValueError):
            op = {k: record.get(k) for k in ("id", "command", "artifact_folder", "started_at")}
            op.update(steps=list(record.get("steps") or []), errors=list(record.get("errors") or []),
                      artifacts=list(record.get("artifacts") or []), needs_input=[])
        for key in ("source", "job_id"):
            if record.get(key):
                op[key] = record[key]
        op["status"] = "running"
        ops.insert(0, op)
        created = True
    info = {"id": operation_id, "command": str((op or {}).get("command") or ""),
            "status": str((op or {}).get("status") or ""), "reason": why, "expired": False}
    if op is None:
        info["message"] = ("This run is no longer on this PC, so there is nothing to "
                           "answer. Send the command again if it is still needed.")
        return info
    if op.get("status") not in ("awaiting_input", "running"):
        info["message"] = (f"This run already ended ({info['status'] or 'unknown'}), so "
                           "there is nothing left to answer. Send the command again if "
                           "it is still needed.")
        return info
    now = datetime.now().isoformat(timespec="seconds")
    message = expired_message(why)
    _mark_expired(op, why, message, now)
    state_store.save("operations", ops)
    from .approval_inbox_service import void_for_operation  # lazy: cycle
    void_for_operation(operation_id, "owning run expired")
    folder_log = Path(str(op.get("artifact_folder") or "")) / "operation_log.json"
    if created and op.get("artifact_folder") and folder_log.parent.is_dir() and not folder_log.exists():
        try:
            folder_log.write_text(json.dumps(op, indent=2, default=str) + "\n", encoding="utf-8")
        except OSError:
            pass
    elif op.get("artifact_folder") and folder_log.is_file():
        try:
            logged = json.loads(folder_log.read_text(encoding="utf-8"))
            if isinstance(logged, dict) and logged.get("id") == operation_id:
                _mark_expired(logged, why, message, now)
                folder_log.write_text(json.dumps(logged, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError):
            pass
    log.info("operation.expired id=%s reason=%s", operation_id, why)
    try:
        from . import jobs_service, push_service  # lazy: jobs_service imports this module
        jobs_service.note_run_expired(op)
        push_service.notify_run_expired(op)
    except Exception:  # noqa: BLE001 — notification is never load-bearing
        log.warning("operator.expiry_notify_failed", exc_info=True)
    info.update(status="failed", expired=True, message=message)
    return info


# v7.7 (0.9.17): what a run IS right now. The window takes a run's status
# from here, never from whether its folder could be read: a run's
# operation_log.json does not exist until it first parks or ends (on
# 2026-09-24 a job run opened 14:25:22-31, before its first park, was
# painted "Failed" for want of that file).
_TERMINAL_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})


def _same_folder(a, b) -> bool:
    if not a or not b:
        return False
    norm = lambda p: os.path.normcase(os.path.normpath(str(p)))  # noqa: E731
    return norm(a) == norm(b)


def live_state(operation_id: str = "", artifact_folder: str = "") -> dict:
    """The run by id (or by its run folder): the live session first — a run
    in flight or parked in memory — then the operations store. ``known`` is
    False only when neither has it. ``pending`` is the open question of a
    run waiting on the owner, so the window can show it from memory."""
    oid = str(operation_id or "")
    sessions = list(_SESSIONS.values())
    session = _SESSIONS.get(oid) if oid else None
    if session is None and artifact_folder:
        session = next((s for s in sessions if _same_folder(s.folder, artifact_folder)), None)
    if session is not None:
        oid = str(session.operator.record.get("id") or oid)
    ops = [o for o in state_store.load_list("operations") if isinstance(o, dict)]
    stored = next((o for o in ops if oid and o.get("id") == oid), None)
    if stored is None and session is None and artifact_folder:
        stored = next((o for o in ops if _same_folder(o.get("artifact_folder"), artifact_folder)), None)
    if session is not None:
        src = session.operator.record
        status = str(src.get("status") or "")
        if src.get("awaiting_input"):
            status = "awaiting_input"
        elif status not in _TERMINAL_STATUSES:
            status = "running"
        folder = str(session.folder)
    elif stored is not None:
        src = stored
        status = str(stored.get("status") or "") or "unknown"
        folder = str(stored.get("artifact_folder") or "")
        oid = str(stored.get("id") or oid)
    else:
        return {"id": oid, "known": False, "status": "unknown", "live": False, "command": "",
                "source": "", "artifact_folder": str(artifact_folder or ""), "pending": None}
    needs = src.get("needs_input") if isinstance(src.get("needs_input"), list) else []
    pending = needs[-1] if status == "awaiting_input" and needs and isinstance(needs[-1], dict) else None
    return {"id": oid, "known": True, "status": status, "live": session is not None,
            "command": str(src.get("command") or ""), "source": str(src.get("source") or ""),
            "artifact_folder": folder, "pending": parked_runs.json_safe(pending) if pending else None}


def recover_parked_runs() -> dict:
    """At startup, before the jobs engine.

    v7.6: every run still waiting either has a usable parked file — it
    stays waiting and the answer rebuilds it — or it expires now, so nothing
    sits "waiting" that can never continue.
    v7.8: a run checkpointed at a step boundary when the app closed is
    rebuilt now and listed in ``resumed`` (the lifespan continues it); a run
    the app closed in the middle of a step expires honestly — on record
    even if it never parked. Files of runs that already ended are removed."""
    kept: list[str] = []
    expired: list[str] = []
    resumed: list[str] = []
    ops = {str(o.get("id")): o for o in state_store.load_list("operations")
           if isinstance(o, dict) and o.get("id")}
    seen: set = set()
    for oid in parked_runs.list_ids():
        if oid in _SESSIONS:
            continue
        seen.add(oid)
        payload = parked_runs.load(oid)
        op = ops.get(oid)
        state = payload["state"] if payload else ""
        ended = op is not None and op.get("status") in _TERMINAL_STATUSES
        if state == parked_runs.CHECKPOINT and not ended:
            _SESSIONS[oid] = _session_from_payload(payload)
            resumed.append(oid)
        elif state == parked_runs.PARKED and op is not None and op.get("status") == "awaiting_input":
            kept.append(oid)
        elif state in (parked_runs.RUNNING, parked_runs.RESUMING) and not ended:
            expire_run(oid, "mid_step" if state == parked_runs.RUNNING else "interrupted",
                       record=payload["record"])
            expired.append(oid)
        elif payload is None and op is not None and op.get("status") == "awaiting_input":
            expire_run(oid, "state_unreadable")
            expired.append(oid)
        else:
            parked_runs.delete(oid)       # the run already ended, or nothing to go on
    for oid, op in ops.items():
        if oid in seen or oid in _SESSIONS or op.get("status") != "awaiting_input":
            continue
        expire_run(oid, "state_missing")
        expired.append(oid)
    log.info("parked_runs.recovered kept=%d expired=%d resumed=%d", len(kept), len(expired), len(resumed))
    return {"kept": kept, "expired": expired, "resumed": resumed}


# ---------------------------------------------------------------------------
# v7.8 (0.9.18): closing the app — checkpoint at a step boundary, resume
# ---------------------------------------------------------------------------

def is_draining() -> bool:
    return _DRAINING


def is_live(operation_id: str) -> bool:
    """A run this process holds in memory (in flight, parked, or resumed)."""
    return str(operation_id or "") in _SESSIONS


async def drain(grace: float = DRAIN_GRACE_SECONDS) -> dict:
    """The app is closing (POST /app/drain, from Electron's before-quit).
    No run starts or resumes from here on; each run in flight finishes its
    current step and is checkpointed at the boundary. Waits up to ``grace``
    seconds. A run still inside a step when the grace runs out is left
    mid-step: its "running" file makes the restart expire it honestly."""
    global _DRAINING
    _DRAINING = True
    log.info("operator.draining in_flight=%d", len(_IN_FLIGHT))
    deadline = time.monotonic() + max(0.0, float(grace))
    while _IN_FLIGHT and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    checkpointed = sorted(oid for oid in list(_SESSIONS)
                          if (parked_runs.load(oid) or {}).get("state") == parked_runs.CHECKPOINT)
    mid_step = sorted(_IN_FLIGHT)
    log.info("operator.drained checkpointed=%d mid_step=%d", len(checkpointed), len(mid_step))
    return {"checkpointed": checkpointed, "mid_step": mid_step}


def end_drain() -> None:
    """Tests (and a quit that was called off): runs may start again."""
    global _DRAINING
    _DRAINING = False


async def _checkpoint(session: "_OperationSession", emit: EmitFn) -> dict:
    record = session.operator.record
    parked_runs.save(session, parked_runs.CHECKPOINT)
    log.info("operation.checkpointed id=%s", record.get("id"))
    await emit({"event": "checkpointed", "data": {"id": record.get("id"), "message": CLOSING_MESSAGE}})
    return {"id": record.get("id"), "checkpointed": True}


def _resume_emit(record: dict) -> EmitFn:
    """Where a resumed run's events go: the PC window's job-run feed, so the
    window can follow it live as it follows a claimed job run."""
    try:
        from . import jobs_service     # lazy: jobs_service imports this module
        return jobs_service.event_sink(str(record.get("id") or ""))
    except Exception:  # noqa: BLE001 — the run never depends on being watched
        return _discard_event


async def resume_checkpointed(operation_id: str) -> dict:
    """v7.8: continue a run checkpointed when the app closed, from exactly
    the step boundary it stopped at — the same conversation, record and
    context, no new message. Scheduled by the lifespan after the sweep."""
    session = _SESSIONS.get(operation_id)
    if session is None or _DRAINING:
        return {}
    apply_to_environment()
    record = session.operator.record
    emit = _resume_emit(record)
    session.operator.emit = emit
    parked_runs.save(session, parked_runs.RUNNING)
    log.info("operation.resuming_checkpoint id=%s", operation_id)
    try:
        from . import jobs_service
        jobs_service.note_run_resumed(record)
    except Exception:  # noqa: BLE001 — notification is never load-bearing
        log.warning("operator.resume_notice_failed", exc_info=True)
    await emit({"event": "start", "data": {
        "id": record.get("id"), "command": record.get("command", ""), "resumed": True,
        "artifact_folder": str(session.folder), "started_at": record.get("started_at", "")}})
    _announce_run(record, "resumed")
    outcome = None
    async with _session_lock(operation_id):
        _IN_FLIGHT.add(operation_id)
        try:
            outcome = await _run_turn(session, list(session.input_list or []))
        except Exception as exc:  # noqa: BLE001
            log.exception("operator.resume_failed id=%s", operation_id)
            msg = f"Planner failed: {type(exc).__name__}: {exc}"
            record["errors"].append(msg)
            await emit({"event": "error", "data": {"message": msg}})
        finally:
            _IN_FLIGHT.discard(operation_id)
    if outcome == CHECKPOINTED:
        return await _checkpoint(session, emit)
    return await _persist_or_pause(emit, record, session.folder)


def _apply_restore_answer(operator: OperatorContext, answer: str) -> str:
    """Resolve a pending backup-restore preview from the operator's resume
    answer — the ONLY writer of record["restore_approved"] /
    ["restore_declined"] (operator_tools._restore_gate checks the flags
    plus the snapshot-id signature in code; the planner can't set them)."""
    rec = operator.record
    if (not rec.get("restore_asked")
            or rec.get("restore_approved") or rec.get("restore_declined")):
        return ""
    a = (answer or "").strip()
    if a == RESTORE_PROCEED or _RESEARCH_APPROVE_RE.match(a):
        rec["restore_approved"] = True
        return ("The operator APPROVED the previewed restore. Call "
                "restore_backup again with the SAME timestamp.")
    if a == RESTORE_CANCEL or _RESEARCH_DECLINE_RE.match(a):
        rec["restore_declined"] = True
        return ("The operator DECLINED the restore. Do not restore; "
                "acknowledge briefly in your receipt.")
    rec["restore_asked"] = False   # unrecognized → re-present on next call
    return ""


def _sanitize_research_model(value: str) -> str:
    """Allowlist the composer's per-run sub-agent model pick (Research and
    Script share the curated list). Anything not on it — junk, an unknown
    model, an empty string — resolves to "" (use the Settings/env default)."""
    v = (value or "").strip()
    return v if v in ALLOWED_RESEARCH_MODELS else ""


def _sanitize_effort(value: str) -> str:
    """Allowlist the composer's per-run effort pick (sub-agents only — the
    planner's effort is deliberately not per-run switchable)."""
    v = (value or "").strip().lower()
    return v if v in ALLOWED_EFFORT_LEVELS else ""


def mark_background(operation_id: str) -> bool:
    """Flip a LIVE session's record to background mode ("Continue in
    background"). Deterministic and additive-only: the flag never opens a
    gate — operator_tools.save_memory READS it to refuse unattended memory
    writes. Returns False when the session is gone (a finished run has
    nothing to flag)."""
    session = _SESSIONS.get(operation_id)
    if session is None:
        return False
    session.operator.record["background"] = True
    log.info("operation.backgrounded id=%s", operation_id)
    return True


async def run_operation(*, command: str, emit: EmitFn, project_id: str = "",
                        research_model: str = "", script_model: str = "",
                        effort: str = "", background: bool = False,
                        origin: "dict | None" = None) -> dict:
    """Run an operator command end to end via the planner agent (first turn).

    ``origin`` (v7.3) is set only by jobs_service for a command the owner sent
    from the Owner Workspace: it STAMPS the record (source + job id) and
    changes nothing else — same planner, tools, gates, ceilings, allowlists."""
    apply_to_environment()
    if not get_effective_value("ANTHROPIC_API_KEY"):
        await emit({"event": "error", "data": {
            "message": "ANTHROPIC_API_KEY is not set. Open Settings to add your Anthropic API key."
        }})
        return {}
    if _DRAINING:
        await emit({"event": "error", "data": {
            "message": "Ridian Operator is closing — send this again after it restarts."}})
        return {}

    command = (command or "").strip()
    if len(command) < 4:
        await emit({"event": "error", "data": {
            "message": "Command is too short. Tell Ridian what to do in plain English."
        }})
        return {}

    folder = create_run_folder(_slug_for_command(command))
    record = operation_log_service.build_record(
        command=command,
        intent="planner",
        artifact_folder=str(folder),
    )
    # v1.9: source-lock detection (see operator_tools._grounding_gate).
    record["source_locked_url"] = detect_source_lock(command)
    # v2.1: addresses the operator explicitly typed in the command are verified
    # recipients for draft_gmail's provenance gate (it never invents one).
    record["user_provided_emails"] = extract_emails(command)
    # v4.0.1: numbers the operator actually typed — the invoice line-item
    # provenance gate verifies every amount/qty/rate against these (never a
    # planner-invented figure). Same pattern as the recipient emails above.
    # v6.7: one absorption path builds BOTH pools (full + plain); dollar
    # figures can sanction amounts/rates but never quantities.
    absorb_stated_numbers(record, command)
    # v2.5: conversational input must get a conversational answer — the build
    # tools refuse (operator_tools._deliverable_gate) unless the command
    # actually asked for a deliverable.
    record["deliverable_intent"] = detect_deliverable_intent(command)
    # v3.1: save_memory's direct-write gate. True only when the OPERATOR's own
    # words command a save ("remember that…", "add a contact…"); the planner's
    # inferred learnings must go through propose_memory_update.
    record["save_intent"] = detect_save_intent(command)
    # v2.8: project grouping. Unknown ids are dropped (never fail the run over
    # organizing metadata).
    record["project_id"] = (
        project_id if operation_log_service.project_exists(project_id) else ""
    )
    # v3: per-run sub-agent overrides from the composer selectors. The tools
    # read these via _effective_*(); the PLANNER model and effort are
    # deliberately not per-run switchable (Settings only, warning attached).
    record["research_model_override"] = _sanitize_research_model(research_model)
    record["script_model_override"] = _sanitize_research_model(script_model)
    record["effort_override"] = _sanitize_effort(effort)
    # v3.2: hard per-run cost fence — the WHOLE operation, planner turns
    # included. Snapshotted at intake so a mid-run Settings edit can't move a
    # fence the operator already saw named in the plan line.
    record["cost_ceiling_usd"] = resolve_cost_ceiling()
    record["spend_usd"] = 0.0
    # v3.6: background (fire-and-forget) run. SAFE-ONLY by construction —
    # every gate still parks the run; this flag only makes save_memory refuse
    # unattended direct writes (route to the proposal queue instead).
    record["background"] = bool(background)
    record["awaiting_input"] = False
    if origin and origin.get("source") == JOB_SOURCE and origin.get("job_id"):
        record["source"] = JOB_SOURCE
        record["job_id"] = str(origin["job_id"])
    await _emit_start(emit, record, command, folder)
    _announce_run(record, "started")

    operator = OperatorContext(folder=folder, record=record, emit=emit)
    # v2.3: if the operator attached a PDF / pasted text before this command,
    # ground the run in it (writes source.md, sets grounding_ok, locks the run).
    staged_note = _consume_staged_source(operator)
    upload_state_line = _compute_upload_state()
    session = _OperationSession(
        operator=operator, folder=folder, system=build_planner_system(),
        input_list=[], upload_state_line=upload_state_line,
    )
    _SESSIONS[record["id"]] = session
    # v7.8: on disk from the start, so a run the app closes mid-step is
    # expired honestly after the restart even if it never parked.
    parked_runs.save(session, parked_runs.RUNNING)

    planner_input = _build_planner_input(command, upload_state_line)
    if staged_note:
        planner_input = staged_note + "\n" + planner_input
    outcome = None
    _IN_FLIGHT.add(record["id"])
    try:
        outcome = await _run_turn(session, [{"role": "user", "content": planner_input}])
    except Exception as exc:  # noqa: BLE001 — top-level safety net
        log.exception("operator.run_failed id=%s", record.get("id"))
        msg = f"Planner failed: {type(exc).__name__}: {exc}"
        record["errors"].append(msg)
        await emit({"event": "error", "data": {"message": msg}})
    finally:
        _IN_FLIGHT.discard(record["id"])

    if outcome == CHECKPOINTED:
        return await _checkpoint(session, emit)
    return await _persist_or_pause(emit, record, folder)


async def continue_operation(*, operation_id: str, answer: str, emit: EmitFn) -> dict:
    """Resume a paused operation with the operator's answer as context.

    Reuses the SAME OperatorContext (folder, record, source-lock + grounding
    flags) and the SDK conversation history, so the run CONTINUES rather than
    starting fresh — the behavioral heart of v2.
    """
    apply_to_environment()
    answer = (answer or "").strip()
    if not answer:
        await emit({"event": "error", "data": {"message": "Type an answer first."}})
        return {}
    if _DRAINING:
        await emit({"event": "error", "data": {"message":
            "Ridian Operator is closing — answer again after it restarts; the question stays open."}})
        return {}

    # v7.6: a parked run survives a restart — its session is rebuilt from the
    # parked file. A run that truly cannot continue is expired (failed,
    # reported, notified) and the window says so and offers "Send again";
    # answering a dead run never ends in a bare error.
    session = _SESSIONS.get(operation_id)
    why = ""
    if session is None:
        session, why = _restore_session(operation_id)
    if session is None:
        await emit({"event": "expired", "data": expire_run(operation_id, why)})
        return {}

    async with _session_lock(operation_id):
        operator = session.operator
        operator.emit = emit                 # rebind to THIS request's SSE stream
        record = operator.record
        record["awaiting_input"] = False     # cleared; set again only if it re-asks
        # v7.8: questions raised from here on are this turn's; the ones
        # before were answered by this answer (one card per pending item).
        record["needs_turn_start"] = len(record.get("needs_input") or [])
        # v7.6: until it parks again or ends, a restart means the run was
        # interrupted mid-way — it must never replay from the question.
        parked_runs.save(session, parked_runs.RESUMING)
        _withdraw_pushes(operation_id, "answered")
        # v2.1: an address the operator types in a resume answer becomes a
        # verified recipient for draft_gmail's provenance gate.
        typed = record.setdefault("user_provided_emails", [])
        for e in extract_emails(answer):
            if e not in typed:
                typed.append(e)
        # v4.0.1: numbers typed in a resume answer become verifiable for the
        # invoice provenance gate ("How many?" → "3" makes 3 user-stated).
        # v6.7: same dual-pool absorption as intake — "$500" in an answer
        # can sanction an amount, never a quantity.
        absorb_stated_numbers(record, answer)
        # v2.5: a resume answer can add deliverable intent ("yes, build the
        # deck") to a run that started conversational. Never downgrades.
        if not record.get("deliverable_intent") and detect_deliverable_intent(answer):
            record["deliverable_intent"] = True
        # v3.1: same for save intent — "yes, remember that" in a resume answer
        # unlocks save_memory for this run. Never downgrades.
        if not record.get("save_intent") and detect_save_intent(answer):
            record["save_intent"] = True

        await emit({"event": "start", "data": {
            "id": record["id"], "command": answer, "resumed": True,
            "artifact_folder": str(session.folder), "started_at": record["started_at"],
        }})
        _announce_run(record, "resumed")

        # Deterministic resume-answer hooks: source-lock relaxation (a: general
        # research → unlock; b: paste) and research-plan approval. Both flip
        # record flags in code — the gates never trust the planner's word.
        notes = [
            n for n in (
                _apply_grounding_answer(operator, answer),
                _apply_research_answer(operator, answer),
                _apply_invoice_answer(operator, answer),
                _apply_proposal_answer(operator, answer),
                _apply_contact_admin_answer(operator, answer),
                _apply_restore_answer(operator, answer),
                _apply_item_selection_answer(operator, answer),
                _apply_sms_answer(operator, answer),
            ) if n
        ]
        # v6.0 Phase 3: an answer given IN-THREAD resolves the staged inbox
        # entry too — the inbox never shows an already-answered item.
        try:
            from . import approval_inbox_service
            approval_inbox_service.sync_from_record(record)
        except Exception:  # noqa: BLE001 — sync must never break the resume
            log.exception("approval_inbox.sync_failed id=%s", operation_id)
        note = "\n\n".join(notes)
        user_content = (
            (note + "\n\n" if note else "")
            + f"The operator's answer to your question: {answer}"
        )
        items = (session.input_list or []) + [{"role": "user", "content": user_content}]

        outcome = None
        _IN_FLIGHT.add(operation_id)
        try:
            outcome = await _run_turn(session, items)
        except Exception as exc:  # noqa: BLE001
            log.exception("operator.continue_failed id=%s", operation_id)
            msg = f"Planner failed: {type(exc).__name__}: {exc}"
            record["errors"].append(msg)
            await emit({"event": "error", "data": {"message": msg}})
        finally:
            _IN_FLIGHT.discard(operation_id)

        if outcome == CHECKPOINTED:
            return await _checkpoint(session, emit)
        return await _persist_or_pause(emit, record, session.folder)
