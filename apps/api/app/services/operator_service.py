"""Operator v1 — natural-command → finished business artifacts.

Replaces the form-based workflow model with a single operation that:

  1. Reads a natural-language command from the operator.
  2. Recognizes the intent (v1: only the AGI-style audiobook is wired up).
  3. Runs research, scriptwriting, and audio synthesis as discrete tools.
  4. Streams structured events to an asyncio Queue so the renderer can
     show a live execution timeline (via SSE).
  5. Writes real artifacts to outputs/<run>/ and appends an operation log.

If a tool fails (no web search, no TTS), the operation degrades honestly:
it emits a failure event, writes a partial operation_log.json, and
returns whatever artifacts it did produce. It does NOT fabricate sources
or fake audio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from agents import Agent, Runner, WebSearchTool, trace

from ..agents import default_model, load_prompt
from . import operation_log_service, tts_service
from .artifact_service import create_run_folder, write_artifact
from .settings_service import apply_to_environment, get_effective_value

log = logging.getLogger("ridian.operator")

EmitFn = Callable[[dict], Awaitable[None]]


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


SUPPORTED_INTENTS = ("agi-audiobook",)


@dataclass
class OperationState:
    """In-flight operation. Lives in memory while the operation runs,
    then is persisted to operations.json via operation_log_service."""

    record: dict
    queue: asyncio.Queue
    done: asyncio.Event = field(default_factory=asyncio.Event)


def recognize_intent(command: str) -> str:
    """v1: simple keyword routing. Returns an intent name or 'unknown'."""
    text = (command or "").lower()
    has_audio = any(w in text for w in ("audiobook", "audio book", "audio overview", "podcast", "notebooklm"))
    has_research = any(w in text for w in ("research", "newest", "latest", "brief", "advances", "what's new", "whats new"))
    # The validation command pattern: "Research the newest in <topic> and give me a NotebookLM-style audiobook"
    if has_audio and has_research:
        return "agi-audiobook"
    # Audio-only commands ("make an audiobook about X") also route to the audiobook intent.
    if has_audio:
        return "agi-audiobook"
    return "unknown"


def extract_topic(command: str, intent: str) -> str:
    """Pull the topic from the command. Cheap heuristics — the script writer
    will gracefully handle whatever ends up here."""
    if intent != "agi-audiobook":
        return command.strip()

    text = (command or "").strip()
    # Strip leading verbs.
    text = re.sub(r"^(research|find|gather|get|tell me about|brief me on|cover)\s+", "", text, flags=re.IGNORECASE)
    # Strip trailing audiobook phrasing.
    text = re.sub(
        r"\s+(and\s+)?(give|make|create|produce|build)\s+me\s+a?\s*(notebooklm[-\s]*style\s+)?(audio\s*book|audiobook|audio\s*overview|podcast).*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Strip "in/about/on" prefixes that often glue the topic on.
    text = re.sub(r"^(in|about|on|the newest in|the latest in|newest in|latest in)\s+", "", text, flags=re.IGNORECASE)
    text = text.strip(" .,;:-")
    return text or "recent AGI advances"


# ---------------------------------------------------------------------------
# Audiobook operation
# ---------------------------------------------------------------------------


async def _emit_step(emit: EmitFn, record: dict, *, name: str, status: str, detail: str = "") -> dict:
    """Append a step to the record and emit an SSE event."""
    now = datetime.now().isoformat(timespec="seconds")
    # Update or insert the step.
    step = next((s for s in record["steps"] if s["name"] == name), None)
    if step is None:
        step = {"name": name, "status": status, "started_at": now, "completed_at": "", "detail": detail}
        record["steps"].append(step)
    else:
        step["status"] = status
        step["detail"] = detail or step.get("detail", "")
        if status in ("completed", "failed", "skipped"):
            step["completed_at"] = now
    await emit({"event": "step", "data": dict(step)})
    return step


async def _emit_artifact(emit: EmitFn, record: dict, *, name: str, path: str, kind: str) -> None:
    artifact = {"name": name, "path": path, "kind": kind}
    record["artifacts"].append(artifact)
    await emit({"event": "artifact", "data": artifact})


async def _emit_message(emit: EmitFn, text: str) -> None:
    await emit({"event": "message", "data": {"text": text}})


async def _emit_error(emit: EmitFn, record: dict, message: str) -> None:
    record["errors"].append(message)
    await emit({"event": "error", "data": {"message": message}})


def _build_research_agent() -> Agent:
    return Agent(
        name="Operator Researcher",
        instructions=load_prompt("operator_research_prompt.txt"),
        model=default_model(),
        tools=[WebSearchTool(search_context_size="high")],
    )


def _build_script_agent() -> Agent:
    return Agent(
        name="Operator Scriptwriter",
        instructions=load_prompt("operator_script_prompt.txt"),
        model=default_model(),
    )


def _count_sources(packet_md: str) -> int:
    return len(re.findall(r"^###\s+\S", packet_md or "", flags=re.MULTILINE))


def _slug_for_command(command: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", " ", command or "").strip()
    base = re.sub(r"\s+", "-", base).lower()
    return f"operator-{base[:60] or 'audiobook'}"


async def _run_agi_audiobook(
    *, command: str, emit: EmitFn, record: dict, folder: Path,
) -> dict:
    """Run the audiobook intent end-to-end. Mutates ``record`` in place."""
    topic = extract_topic(command, "agi-audiobook")

    # ----- 1. interpret -----
    await _emit_step(emit, record, name="interpret", status="completed",
                     detail=f"Topic: {topic}. Plan: web research → sources packet → "
                            f"two-host script → audio synthesis → local save.")

    # ----- 2. research -----
    await _emit_step(emit, record, name="research", status="running",
                     detail=f"Searching the live web for: {topic}")
    record["tools_used"].append("web_search")

    research_agent = _build_research_agent()
    research_agent.model = default_model()
    research_input = (
        f"Operator command: {command}\n\n"
        f"Topic to research: {topic}\n\n"
        "Produce the sources packet now."
    )
    sources_packet = ""
    try:
        with trace("ridian.operator.research"):
            res = await Runner.run(research_agent, input=research_input)
        sources_packet = (res.final_output or "").strip()
    except Exception as exc:
        await _emit_step(emit, record, name="research", status="failed",
                         detail=f"Web research failed: {type(exc).__name__}: {exc}")
        await _emit_error(emit, record, "Web research failed or is not configured.")
        # Still write a stub packet so the user can see what we tried.
        sources_packet = (
            f"# Sources Packet\n\n"
            f"## Retrieval\n- Topic: {topic}\n- Retrieval date: {datetime.now().date().isoformat()}\n\n"
            f"## Sources\n\n_Web research failed: {type(exc).__name__}._\n\n"
            "The operator chose not to fabricate sources. Connect web-search-capable "
            "model access and rerun the command.\n"
        )

    src_path = folder / "sources_packet.md"
    write_artifact(folder, "sources_packet.md", sources_packet)
    record["sources_count"] = _count_sources(sources_packet)
    record["tools_used"].append("write_file")
    await _emit_artifact(emit, record, name="sources_packet.md",
                         path=str(src_path), kind="markdown")
    if "research" in {s["name"] for s in record["steps"] if s["status"] == "running"}:
        await _emit_step(emit, record, name="research", status="completed",
                         detail=f"{record['sources_count']} sources gathered.")

    research_failed = record["sources_count"] == 0

    # ----- 3. write script -----
    await _emit_step(emit, record, name="script", status="running",
                     detail="Writing two-host conversational audiobook script.")
    script_md = ""
    try:
        script_agent = _build_script_agent()
        script_agent.model = default_model()
        script_input = (
            f"Operator command: {command}\n\n"
            f"Sources packet:\n\n{sources_packet}\n"
        )
        with trace("ridian.operator.script"):
            res2 = await Runner.run(script_agent, input=script_input)
        script_md = (res2.final_output or "").strip()
    except Exception as exc:
        await _emit_step(emit, record, name="script", status="failed",
                         detail=f"Script generation failed: {type(exc).__name__}: {exc}")
        await _emit_error(emit, record, f"Script generation failed: {exc}")

    if not script_md:
        # Fallback: a one-line script that admits the failure honestly.
        script_md = (
            f"# Audiobook unavailable\n\n"
            "**Host A**: We weren't able to put together a real script for this run. "
            "Reconnect web search or rerun the command.\n"
        )

    script_path = folder / "script.md"
    write_artifact(folder, "script.md", script_md)
    record["tools_used"].append("write_file")
    await _emit_artifact(emit, record, name="script.md",
                         path=str(script_path), kind="markdown")
    if "script" in {s["name"] for s in record["steps"] if s["status"] == "running"}:
        runtime = tts_service.estimate_runtime_seconds(script_md)
        await _emit_step(emit, record, name="script", status="completed",
                         detail=f"Script ready. Estimated spoken runtime ≈ {runtime // 60} min {runtime % 60} sec.")

    # ----- 4. synthesize audio -----
    audio_path = folder / "audiobook.mp3"
    audio_ok = False
    if research_failed:
        await _emit_step(emit, record, name="audio", status="skipped",
                         detail="Skipped audio synthesis because web research returned no sources.")
    else:
        await _emit_step(emit, record, name="audio", status="running",
                         detail="Synthesizing two-voice MP3 via OpenAI TTS.")
        record["tools_used"].append("tts")
        try:
            # Run blocking TTS in a worker thread; emit progress via run-loop callback.
            loop = asyncio.get_running_loop()

            def progress_cb(done: int, total: int) -> None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        _emit_step(
                            emit, record, name="audio", status="running",
                            detail=f"Synthesizing… segment {done}/{total}.",
                        ),
                        loop,
                    )
                except RuntimeError:
                    pass

            meta = await asyncio.to_thread(
                tts_service.synthesize_audiobook,
                script_md,
                audio_path,
                progress_cb=progress_cb,
            )
            audio_ok = True
            record["audio_generated"] = True
            record["audio_duration_seconds"] = tts_service.estimate_runtime_seconds(script_md)
            await _emit_artifact(emit, record, name="audiobook.mp3",
                                 path=str(audio_path), kind="audio")
            await _emit_step(emit, record, name="audio", status="completed",
                             detail=f"{meta['segments']} segments, {meta['bytes'] // 1024} KB, "
                                    f"voices {meta['voices'][0]}/{meta['voices'][1]}.")
        except tts_service.TTSError as exc:
            await _emit_step(emit, record, name="audio", status="failed", detail=exc.detail)
            await _emit_error(emit, record, f"Audio synthesis failed: {exc.detail}")
        except Exception as exc:  # noqa: BLE001
            await _emit_step(emit, record, name="audio", status="failed",
                             detail=f"{type(exc).__name__}: {exc}")
            await _emit_error(emit, record, f"Audio synthesis failed: {exc}")

    # ----- 5. write operation_log.json -----
    if audio_ok or record["sources_count"] > 0:
        record["status"] = "completed" if (audio_ok and record["sources_count"] > 0) else "partial"
    else:
        record["status"] = "failed"

    log_path = folder / "operation_log.json"
    # Snapshot a finalized copy on disk (without queue/emit machinery).
    log_path.write_text(json.dumps(_finalized_view(record), indent=2) + "\n", encoding="utf-8")
    record["tools_used"].append("write_file")
    await _emit_artifact(emit, record, name="operation_log.json",
                         path=str(log_path), kind="json")
    return record


def _finalized_view(record: dict) -> dict:
    """A clean serializable snapshot for disk + the SSE 'complete' event."""
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
    }


# ---------------------------------------------------------------------------
# Entry point used by the FastAPI streaming endpoint
# ---------------------------------------------------------------------------


async def run_operation(*, command: str, emit: EmitFn) -> dict:
    """Run a single operation end to end. ``emit`` streams SSE events.

    Returns the finalized record. Always persists to the operation log
    (success or failure) so the renderer's Recent Operations stays honest.
    """
    apply_to_environment()
    if not get_effective_value("OPENAI_API_KEY"):
        await emit({"event": "error", "data": {
            "message": "OPENAI_API_KEY is not set. Open Settings to add your key."
        }})
        return {}

    command = (command or "").strip()
    intent = recognize_intent(command)
    if intent not in SUPPORTED_INTENTS:
        await emit({"event": "error", "data": {
            "message": (
                "Operator v1 only handles the audiobook intent right now. "
                "Try something like: 'Research the newest in AGI and give me "
                "a NotebookLM-style audiobook.'"
            )
        }})
        return {}

    folder = create_run_folder(_slug_for_command(command))
    record = operation_log_service.build_record(
        command=command, intent=intent, artifact_folder=str(folder)
    )
    await emit({"event": "start", "data": {
        "id": record["id"],
        "command": command,
        "intent": intent,
        "artifact_folder": str(folder),
        "started_at": record["started_at"],
    }})

    try:
        await _run_agi_audiobook(command=command, emit=emit, record=record, folder=folder)
    except Exception as exc:  # noqa: BLE001 — top-level safety net
        log.exception("operator.run_failed id=%s", record.get("id"))
        record["errors"].append(f"Unhandled error: {type(exc).__name__}: {exc}")
        record["status"] = "failed"
        await emit({"event": "error", "data": {"message": str(exc)}})

    operation_log_service.finalize(record, status=record["status"])
    # Re-write the on-disk operation_log.json so it carries the final status.
    try:
        (folder / "operation_log.json").write_text(
            json.dumps(_finalized_view(record), indent=2) + "\n", encoding="utf-8",
        )
    except OSError:
        pass

    operation_log_service.append_operation(_finalized_view(record))

    await emit({"event": "complete", "data": _finalized_view(record)})
    return _finalized_view(record)
