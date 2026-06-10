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
from .settings_service import apply_to_environment, get_bool_setting, get_effective_value

log = logging.getLogger("ridian.operator")

EmitFn = Callable[[dict], Awaitable[None]]

# Per-operation safety rail. The planner prompt caps itself at 6 tool calls
# but the SDK also enforces max_turns, so a runaway agent can't loop forever.
_MAX_PLANNER_TURNS = 12


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

    try:
        brand = memory_service.get_brand() or {}
        defined = [k for k in ("ridian", "open_gulf", "buns")
                   if (brand.get(k, {}).get("voice") or "").strip()]
        if defined:
            parts.append("Brand voices defined: " + ", ".join(defined) + ".")
        else:
            parts.append("Brand voices: none defined yet.")
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
            # so the renderer can show a one-line operator receipt at the end.
            try:
                from agents.items import ItemHelpers
                text = ItemHelpers.text_message_output(item).strip()
            except Exception:
                text = ""
            if text:
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
    has_audio = record["audio_generated"]
    has_sources = record["sources_count"] > 0
    has_any_artifact = bool(record["artifacts"])
    has_errors = bool(record["errors"])

    if has_errors and not has_any_artifact:
        record["status"] = "failed"
    elif has_errors:
        record["status"] = "partial"
    elif has_any_artifact:
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

    operation_log_service.append_operation(snapshot)
    await emit({"event": "complete", "data": snapshot})
    return snapshot


async def run_operation(*, command: str, emit: EmitFn) -> dict:
    """Run an operator command end to end via the planner agent."""
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
        intent="planner",  # v1.1: no keyword intent — the planner decides
        artifact_folder=str(folder),
    )
    await _emit_start(emit, record, command, folder)

    operator = OperatorContext(folder=folder, record=record, emit=emit)

    # v1.4: auto-upload state. The planner reads this verbatim and decides
    # whether to call auto_upload_drive at the end of the run. Two gates:
    # the user's setting (default ON) and whether Google Drive is actually
    # connected. If either is false, the line below tells the planner to skip.
    auto_upload_on = get_bool_setting("operator_auto_upload_drive", default=True)
    try:
        drive_connected = bool(google_drive_service.get_status().get("connected"))
    except Exception:
        drive_connected = False
    if auto_upload_on and drive_connected:
        upload_state_line = "Drive auto-upload: on (Google Drive connected). Call auto_upload_drive after artifacts."
    elif auto_upload_on and not drive_connected:
        upload_state_line = "Drive auto-upload: on, BUT Google Drive is not connected. Skip auto_upload_drive and mention in your summary."
    else:
        upload_state_line = "Drive auto-upload: off (user disabled). Skip auto_upload_drive."

    # Capability discovery: the planner needs the command + a reminder that
    # the registry list in its system prompt is the entire toolset, plus a
    # snapshot of what Ridian already remembers so it doesn't re-propose
    # facts already on file (or repeat yesterday's brief).
    planner_input = (
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

    try:
        agent = build_planner_agent()
        streamed = Runner.run_streamed(
            agent,
            input=planner_input,
            context=operator,
            max_turns=_MAX_PLANNER_TURNS,
        )
        await _drain_planner_events(streamed, operator)
    except Exception as exc:  # noqa: BLE001 — top-level safety net
        log.exception("operator.run_failed id=%s", record.get("id"))
        msg = f"Planner failed: {type(exc).__name__}: {exc}"
        record["errors"].append(msg)
        await emit({"event": "error", "data": {"message": msg}})

    return await _persist_and_complete(emit, record, folder)
