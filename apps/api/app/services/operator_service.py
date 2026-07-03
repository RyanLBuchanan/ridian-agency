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
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from agents import Runner
from agents.items import (
    MessageOutputItem,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.stream_events import RunItemStreamEvent

from ..agents.planner_agent import build_planner_agent
from . import gmail_service, google_drive_service, memory_service, operation_log_service
from .artifact_service import create_run_folder
from .operator_context import OperatorContext
from .operator_tools import detect_source_lock, extract_emails
from .settings_service import apply_to_environment, get_bool_setting, get_effective_value

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
    agent: object
    input_list: list
    upload_state_line: str


_SESSIONS: dict[str, _OperationSession] = {}
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def _session_lock(operation_id: str) -> asyncio.Lock:
    lock = _SESSION_LOCKS.get(operation_id)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[operation_id] = lock
    return lock


def _drop_session(operation_id: str) -> None:
    _SESSIONS.pop(operation_id, None)
    _SESSION_LOCKS.pop(operation_id, None)


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
    try:
        profile = memory_service.get_profile()
        filled = {k: v for k, v in profile.items() if (v or "").strip()}
        if filled:
            labels = {
                "operator": "Who", "business": "Business", "offerings": "Sells",
                "customers": "Customers", "goal": "Quarter goal",
                "avoid": "Not interested in", "notes": "Notes",
            }
            lines = [f"  {labels.get(k, k)}: {v.strip()}" for k, v in filled.items()]
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


async def _drain_planner_events(streamed, operator: OperatorContext) -> None:
    """Translate Agents SDK stream events into Operator timeline events.

    The SDK emits ``RunItemStreamEvent`` for each ``ToolCallItem`` /
    ``ToolCallOutputItem`` / ``MessageOutputItem`` produced during the run.
    Tools emit their own step + artifact SSE events from inside their bodies,
    so this translator is mostly about surfacing the planner's *meta*
    decisions (which tool it just called, the final summary message).
    """
    async for event in streamed.stream_events():
        if not isinstance(event, RunItemStreamEvent):
            continue
        item = event.item
        if isinstance(item, ToolCallItem):
            # Tool name is on the raw call; surface a lightweight marker so the
            # renderer can show "Planner: calling <tool>" if it ever wants to.
            try:
                name = getattr(item.raw_item, "name", "(unknown)")
            except Exception:
                name = "(unknown)"
            await operator.emit({
                "event": "message",
                "data": {"text": f"Planner → calling tool: {name}"},
            })
        elif isinstance(item, ToolCallOutputItem):
            # Tool already emitted step/artifact events from inside its body;
            # nothing more to do here. Kept in the dispatch for future use.
            pass
        elif isinstance(item, MessageOutputItem):
            # The planner's final summary message. Render as a 'message' event
            # AND capture it on the record so the receipt survives reloads and
            # so receipt-only runs (questions answered from memory) count as
            # completed rather than failed.
            try:
                from agents.items import ItemHelpers
                text = ItemHelpers.text_message_output(item).strip()
            except Exception:
                text = ""
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
        (folder / "operation_log.json").write_text(
            json.dumps(snapshot, indent=2) + "\n", encoding="utf-8",
        )
        # The operation_log.json itself is an artifact the renderer should see.
        if not any(a["name"] == "operation_log.json" for a in record["artifacts"]):
            await emit({"event": "artifact", "data": {
                "name": "operation_log.json",
                "path": str(folder / "operation_log.json"),
                "kind": "json",
            }})
            record["artifacts"].append({
                "name": "operation_log.json",
                "path": str(folder / "operation_log.json"),
                "kind": "json",
            })
            snapshot = _finalized_view(record)
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
    return (
        f"Operator command:\n{command}\n\n"
        f"Current memory + recent operations:\n{_memory_context_snippet()}\n\n"
        f"Auto-upload state: {upload_state_line}\n\n"
        "Plan the minimum-viable sequence of tool calls from your registry, "
        "execute them, verify each result, and then give a short final "
        "receipt of what landed on disk. Do not invent tools.\n\n"
        "After the artifact tools finish (and only if the operation produced "
        "real artifacts), you MAY call propose_memory_update for up to three "
        "items the user would want Ridian to remember from this run — facts, "
        "contacts, follow-ups, or decisions that surfaced organically. Do not "
        "propose anything that was already in memory above. Do not invent."
    )


async def _run_turn(session: _OperationSession, input_items) -> None:
    """Run ONE planner turn against the session's context, streaming events, and
    save the resulting SDK conversation history so a /continue can resume it."""
    streamed = Runner.run_streamed(
        session.agent,
        input=input_items,
        context=session.operator,
        max_turns=_MAX_PLANNER_TURNS,
    )
    await _drain_planner_events(streamed, session.operator)
    try:
        session.input_list = streamed.to_input_list()
    except Exception:  # noqa: BLE001 — keep the prior list if the SDK can't
        pass


async def _persist_or_pause(emit: EmitFn, record: dict, folder: Path) -> dict:
    """If the turn ended paused-awaiting-the-user, snapshot as 'awaiting_input'
    and KEEP the session for a /continue. Otherwise finalize and drop it."""
    if record.get("awaiting_input"):
        record["status"] = "awaiting_input"
        snapshot = _finalized_view(record)
        try:
            (folder / "operation_log.json").write_text(
                json.dumps(snapshot, indent=2) + "\n", encoding="utf-8",
            )
        except OSError:
            pass
        operation_log_service.upsert_operation(snapshot)
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
    session = _SESSIONS.get(operation_id)
    if not session:
        return
    try:
        (session.folder / "source.pdf").write_bytes(data or b"")
    except OSError:
        pass


def _consume_staged_source(operator: OperatorContext) -> str:
    """If a source is staged, ground the run in it and return a planner note+text
    to prepend to the command. Clears the staged source. "" when none."""
    global _STAGED_SOURCE
    if not _STAGED_SOURCE:
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


async def run_operation(*, command: str, emit: EmitFn) -> dict:
    """Run an operator command end to end via the planner agent (first turn)."""
    apply_to_environment()
    if not get_effective_value("OPENAI_API_KEY"):
        await emit({"event": "error", "data": {
            "message": "OPENAI_API_KEY is not set. Open Settings to add your key."
        }})
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
    record["awaiting_input"] = False
    await _emit_start(emit, record, command, folder)

    operator = OperatorContext(folder=folder, record=record, emit=emit)
    # v2.3: if the operator attached a PDF / pasted text before this command,
    # ground the run in it (writes source.md, sets grounding_ok, locks the run).
    staged_note = _consume_staged_source(operator)
    upload_state_line = _compute_upload_state()
    session = _OperationSession(
        operator=operator, folder=folder, agent=build_planner_agent(),
        input_list=[], upload_state_line=upload_state_line,
    )
    _SESSIONS[record["id"]] = session

    planner_input = _build_planner_input(command, upload_state_line)
    if staged_note:
        planner_input = staged_note + "\n" + planner_input
    try:
        await _run_turn(session, planner_input)
    except Exception as exc:  # noqa: BLE001 — top-level safety net
        log.exception("operator.run_failed id=%s", record.get("id"))
        msg = f"Planner failed: {type(exc).__name__}: {exc}"
        record["errors"].append(msg)
        await emit({"event": "error", "data": {"message": msg}})

    return await _persist_or_pause(emit, record, folder)


async def continue_operation(*, operation_id: str, answer: str, emit: EmitFn) -> dict:
    """Resume a paused operation with the operator's answer as context.

    Reuses the SAME OperatorContext (folder, record, source-lock + grounding
    flags) and the SDK conversation history, so the run CONTINUES rather than
    starting fresh — the behavioral heart of v2.
    """
    apply_to_environment()
    session = _SESSIONS.get(operation_id)
    if session is None:
        await emit({"event": "error", "data": {"message":
            "That operation is no longer active — start a new command instead."}})
        return {}

    answer = (answer or "").strip()
    if not answer:
        await emit({"event": "error", "data": {"message": "Type an answer first."}})
        return {}

    async with _session_lock(operation_id):
        operator = session.operator
        operator.emit = emit                 # rebind to THIS request's SSE stream
        record = operator.record
        record["awaiting_input"] = False     # cleared; set again only if it re-asks
        # v2.1: an address the operator types in a resume answer becomes a
        # verified recipient for draft_gmail's provenance gate.
        typed = record.setdefault("user_provided_emails", [])
        for e in extract_emails(answer):
            if e not in typed:
                typed.append(e)

        await emit({"event": "start", "data": {
            "id": record["id"], "command": answer, "resumed": True,
            "artifact_folder": str(session.folder), "started_at": record["started_at"],
        }})

        # Source-lock resume relaxation (a: general research → unlock; b: paste).
        note = _apply_grounding_answer(operator, answer)
        user_content = (
            (note + "\n\n" if note else "")
            + f"The operator's answer to your question: {answer}"
        )
        items = (session.input_list or []) + [{"role": "user", "content": user_content}]

        try:
            await _run_turn(session, items)
        except Exception as exc:  # noqa: BLE001
            log.exception("operator.continue_failed id=%s", operation_id)
            msg = f"Planner failed: {type(exc).__name__}: {exc}"
            record["errors"].append(msg)
            await emit({"event": "error", "data": {"message": msg}})

        return await _persist_or_pause(emit, record, session.folder)
