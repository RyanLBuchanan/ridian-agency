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
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from ..agents import ALLOWED_EFFORT_LEVELS, ALLOWED_RESEARCH_MODELS, default_model
from ..agents.planner_agent import build_planner_system
from . import gmail_service, google_drive_service, memory_service, operation_log_service
from .anthropic_runtime import date_line, estimate_cost_usd, get_client
from .artifact_service import create_run_folder
from .operator_context import OperatorContext, set_current_operator
from .operator_tools import (
    PLANNER_TOOLS,
    RESEARCH_PLAN_CANCEL,
    RESEARCH_PLAN_PROCEED,
    detect_deliverable_intent,
    detect_source_lock,
    extract_emails,
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
        # v3.2: the run's dollar ledger — accumulated planner turns, sub-agent
        # calls, AND failed calls' partials, against the ceiling snapshotted
        # at intake. Persisted so every run's true cost survives in history.
        "spend_usd": round(float(record.get("spend_usd", 0.0) or 0.0), 4),
        "cost_ceiling_usd": record.get("cost_ceiling_usd"),
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
                return
        if last is None or last.stop_reason != "pause_turn" or restarts >= 3:
            break
        restarts += 1

    session.input_list = messages


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
    msg = (f"Run stopped at the cost ceiling — ≈${rec['spend_usd']:.2f} spent "
           f"of the ${ceiling:.2f} limit. Raise it in Settings (or set it to "
           f"'off') and run again.")
    rec["errors"].append(msg)
    await operator.emit_step(name="cost_ceiling", status="failed", detail=msg)
    await operator.emit_error(msg)
    return True


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


async def run_operation(*, command: str, emit: EmitFn, project_id: str = "",
                        research_model: str = "", script_model: str = "",
                        effort: str = "") -> dict:
    """Run an operator command end to end via the planner agent (first turn)."""
    apply_to_environment()
    if not get_effective_value("ANTHROPIC_API_KEY"):
        await emit({"event": "error", "data": {
            "message": "ANTHROPIC_API_KEY is not set. Open Settings to add your Anthropic API key."
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
    # v2.5: conversational input must get a conversational answer — the build
    # tools refuse (operator_tools._deliverable_gate) unless the command
    # actually asked for a deliverable.
    record["deliverable_intent"] = detect_deliverable_intent(command)
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
    record["awaiting_input"] = False
    await _emit_start(emit, record, command, folder)

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

    planner_input = _build_planner_input(command, upload_state_line)
    if staged_note:
        planner_input = staged_note + "\n" + planner_input
    try:
        await _run_turn(session, [{"role": "user", "content": planner_input}])
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
        # v2.5: a resume answer can add deliverable intent ("yes, build the
        # deck") to a run that started conversational. Never downgrades.
        if not record.get("deliverable_intent") and detect_deliverable_intent(answer):
            record["deliverable_intent"] = True

        await emit({"event": "start", "data": {
            "id": record["id"], "command": answer, "resumed": True,
            "artifact_folder": str(session.folder), "started_at": record["started_at"],
        }})

        # Deterministic resume-answer hooks: source-lock relaxation (a: general
        # research → unlock; b: paste) and research-plan approval. Both flip
        # record flags in code — the gates never trust the planner's word.
        notes = [
            n for n in (
                _apply_grounding_answer(operator, answer),
                _apply_research_answer(operator, answer),
            ) if n
        ]
        note = "\n\n".join(notes)
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
