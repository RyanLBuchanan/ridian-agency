"""Operator tool registry — every tool produces a real side effect.

Per the redesign memo, §13: "Every tool in the registry must produce a side
effect (file written, draft created, upload completed) or it doesn't ship.
No tool whose output is 'here's a prompt.'"

These tools are decorated with ``@function_tool`` so the OpenAI Agents SDK
exposes them to the planner agent. Each tool receives a
``RunContextWrapper[OperatorContext]`` as its first argument and reads the
run folder + emits SSE timeline events through that context.

Tools are intentionally narrow:
    web_research            — live web search, returns structured sources Markdown
    write_sources_packet    — writes sources_packet.md to disk
    write_audiobook_script  — generates a two-host script via a sub-agent
    synthesize_audio        — writes audiobook.mp3 via OpenAI TTS
    write_file              — generic allowlisted artifact writer

The planner does NOT see WebSearchTool or the script-writer sub-agent
directly — they're encapsulated inside ``web_research`` and
``write_audiobook_script`` so the planner's tool list stays small and
business-shaped rather than infrastructure-shaped.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from agents import Agent, RunContextWrapper, Runner, WebSearchTool, function_tool, trace

from ..agents import default_model, load_prompt
from . import gmail_service, tts_service
from .artifact_service import write_artifact
from .operator_context import ALLOWED_PROPOSAL_KINDS, OperatorContext

log = logging.getLogger("ridian.operator.tools")


# Files the planner is allowed to write via the generic write_file tool.
# Anything outside this list is rejected so a hallucinating planner can't
# write arbitrary files inside the run folder.
_WRITE_FILE_ALLOWLIST: frozenset[str] = frozenset({
    "sources_packet.md",
    "script.md",
    "operation_log.json",
    # Per-command-shape extras the planner may legitimately produce.
    "brief.md",
    "summary.md",
})


# ---------------------------------------------------------------------------
# Internal sub-agents (not exposed to the planner directly)
# ---------------------------------------------------------------------------


def _research_subagent() -> Agent:
    """The sources-packet writer. Owns WebSearchTool; planner doesn't see it."""
    return Agent(
        name="Operator Researcher",
        instructions=load_prompt("operator_research_prompt.txt"),
        model=default_model(),
        tools=[WebSearchTool(search_context_size="high")],
    )


def _script_subagent() -> Agent:
    return Agent(
        name="Operator Scriptwriter",
        instructions=load_prompt("operator_script_prompt.txt"),
        model=default_model(),
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@function_tool
async def web_research(
    ctx: RunContextWrapper[OperatorContext],
    topic: str,
    time_window: str = "last 30 days",
    depth: str = "strategic",
) -> dict:
    """Run live web research on ``topic`` and return a finished sources packet.

    Uses the OpenAI hosted WebSearchTool through an internal sub-agent. The
    returned ``sources_md`` is a Markdown sources packet ready to be passed
    to ``write_sources_packet`` or ``write_audiobook_script``. Source URLs
    are cited; confidence flags are included.

    Args:
        topic: The subject to research.
        time_window: How recent to skew (default "last 30 days").
        depth: One of "quick" | "strategic" | "deep" — controls source count.

    Returns:
        {"sources_md": str, "sources_count": int}
    """
    operator = ctx.context
    operator.note_tool("web_research")
    await operator.emit_step(
        name="research", status="running",
        detail=f"Searching the live web — topic: {topic} ({time_window}, {depth}).",
    )

    agent = _research_subagent()
    agent.model = default_model()
    prompt = (
        f"Operator command topic: {topic}\n"
        f"Time window: {time_window}\n"
        f"Depth: {depth}\n\n"
        "Produce the sources packet now."
    )
    try:
        with trace("ridian.operator.tool.web_research"):
            result = await Runner.run(agent, input=prompt)
        sources_md = (result.final_output or "").strip()
    except Exception as exc:
        await operator.emit_step(name="research", status="failed",
                                 detail=f"Web research failed: {type(exc).__name__}: {exc}")
        await operator.emit_error(f"web_research failed: {exc}")
        return {"sources_md": "", "sources_count": 0, "error": str(exc)}

    # Verification: count "### " headings (one per source per prompt spec).
    import re
    count = len(re.findall(r"^###\s+\S", sources_md, flags=re.MULTILINE))

    operator.sources_packet_text = sources_md
    operator.record["sources_count"] = count
    await operator.emit_step(name="research", status="completed",
                             detail=f"{count} sources gathered.")
    return {"sources_md": sources_md, "sources_count": count}


@function_tool
async def write_sources_packet(
    ctx: RunContextWrapper[OperatorContext],
    content: str,
) -> dict:
    """Write a sources packet to ``sources_packet.md`` in the run folder.

    Use after ``web_research``. The planner can pass the ``sources_md`` from
    research directly into this tool's ``content`` argument.

    Returns:
        {"path": str, "bytes": int}
    """
    operator = ctx.context
    operator.note_tool("write_file")
    if not content or not content.strip():
        await operator.emit_error("write_sources_packet called with empty content; skipping.")
        return {"path": "", "bytes": 0, "error": "empty content"}

    path = operator.folder / "sources_packet.md"
    write_artifact(operator.folder, "sources_packet.md", content)
    size = path.stat().st_size
    operator.sources_packet_text = content
    await operator.emit_artifact(name="sources_packet.md", path=str(path), kind="markdown")
    return {"path": str(path), "bytes": size}


@function_tool
async def write_audiobook_script(
    ctx: RunContextWrapper[OperatorContext],
    sources_md: str,
    target_minutes: int = 15,
) -> dict:
    """Generate a NotebookLM-style two-host script from a sources packet.

    Writes ``script.md`` to the run folder. The script uses ``**Host A**:``
    and ``**Host B**:`` markers the synthesize_audio tool relies on.

    Returns:
        {"path": str, "bytes": int, "estimated_seconds": int}
    """
    operator = ctx.context
    operator.note_tool("write_audiobook_script")
    if not sources_md or not sources_md.strip():
        await operator.emit_error("write_audiobook_script called without sources; skipping.")
        return {"path": "", "bytes": 0, "estimated_seconds": 0, "error": "no sources"}

    await operator.emit_step(name="script", status="running",
                             detail=f"Writing two-host script (~{target_minutes} min target).")

    agent = _script_subagent()
    agent.model = default_model()
    prompt = (
        f"Sources packet:\n\n{sources_md}\n\n"
        f"Target spoken runtime: ~{target_minutes} minutes.\n"
        "Produce the audiobook script now."
    )
    try:
        with trace("ridian.operator.tool.write_audiobook_script"):
            result = await Runner.run(agent, input=prompt)
        script_md = (result.final_output or "").strip()
    except Exception as exc:
        await operator.emit_step(name="script", status="failed",
                                 detail=f"Script generation failed: {type(exc).__name__}: {exc}")
        await operator.emit_error(f"write_audiobook_script failed: {exc}")
        return {"path": "", "bytes": 0, "estimated_seconds": 0, "error": str(exc)}

    path = operator.folder / "script.md"
    write_artifact(operator.folder, "script.md", script_md)
    size = path.stat().st_size
    runtime = tts_service.estimate_runtime_seconds(script_md)
    operator.script_text = script_md
    await operator.emit_artifact(name="script.md", path=str(path), kind="markdown")
    await operator.emit_step(name="script", status="completed",
                             detail=f"Script ready. Estimated spoken runtime ≈ "
                                    f"{runtime // 60} min {runtime % 60} sec.")
    return {"path": str(path), "bytes": size, "estimated_seconds": runtime}


@function_tool
async def synthesize_audio(
    ctx: RunContextWrapper[OperatorContext],
    script_md: str,
    voice_a: str = "onyx",
    voice_b: str = "nova",
) -> dict:
    """Synthesize a two-voice audiobook MP3 from a script.

    Writes ``audiobook.mp3`` to the run folder using OpenAI TTS. The script
    must include ``**Host A**:`` and ``**Host B**:`` speaker markers (the
    planner should pass the output of ``write_audiobook_script``).

    Returns:
        {"path": str, "bytes": int, "segments": int}
    """
    operator = ctx.context
    operator.note_tool("synthesize_audio")
    if not script_md or not script_md.strip():
        await operator.emit_error("synthesize_audio called with empty script; skipping.")
        return {"path": "", "bytes": 0, "segments": 0, "error": "empty script"}

    audio_path = operator.folder / "audiobook.mp3"
    await operator.emit_step(name="audio", status="running",
                             detail="Synthesizing two-voice MP3 via OpenAI TTS.")
    loop = asyncio.get_running_loop()

    def progress_cb(done: int, total: int) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                operator.emit_step(name="audio", status="running",
                                   detail=f"Synthesizing… segment {done}/{total}."),
                loop,
            )
        except RuntimeError:
            pass

    try:
        meta = await asyncio.to_thread(
            tts_service.synthesize_audiobook,
            script_md, audio_path,
            voice_a=voice_a, voice_b=voice_b,
            progress_cb=progress_cb,
        )
    except tts_service.TTSError as exc:
        await operator.emit_step(name="audio", status="failed", detail=exc.detail)
        await operator.emit_error(f"synthesize_audio failed: {exc.detail}")
        return {"path": "", "bytes": 0, "segments": 0, "error": exc.detail}
    except Exception as exc:
        await operator.emit_step(name="audio", status="failed",
                                 detail=f"{type(exc).__name__}: {exc}")
        await operator.emit_error(f"synthesize_audio failed: {exc}")
        return {"path": "", "bytes": 0, "segments": 0, "error": str(exc)}

    operator.record["audio_generated"] = True
    operator.record["audio_duration_seconds"] = tts_service.estimate_runtime_seconds(script_md)
    await operator.emit_artifact(name="audiobook.mp3", path=str(audio_path), kind="audio")
    await operator.emit_step(
        name="audio", status="completed",
        detail=f"{meta['segments']} segments, {meta['bytes'] // 1024} KB, "
               f"voices {meta['voices'][0]}/{meta['voices'][1]}.",
    )
    return {"path": str(audio_path), "bytes": meta["bytes"], "segments": meta["segments"]}


@function_tool
async def write_file(
    ctx: RunContextWrapper[OperatorContext],
    filename: str,
    content: str,
    kind: str = "markdown",
) -> dict:
    """Write an allowlisted file into the run folder.

    Use this when the planner wants to save an artifact that doesn't have a
    dedicated tool (e.g., ``brief.md`` for a research-only command).
    ``filename`` must be in the allowlist; the planner cannot write arbitrary
    paths or escape the run folder.

    Returns:
        {"path": str, "bytes": int}
    """
    operator = ctx.context
    operator.note_tool("write_file")
    if filename not in _WRITE_FILE_ALLOWLIST:
        await operator.emit_error(
            f"write_file rejected: {filename!r} not in allowlist "
            f"({sorted(_WRITE_FILE_ALLOWLIST)})."
        )
        return {"path": "", "bytes": 0, "error": "filename not allowlisted"}
    if not content or not content.strip():
        await operator.emit_error(f"write_file({filename}) called with empty content; skipping.")
        return {"path": "", "bytes": 0, "error": "empty content"}

    path = operator.folder / filename
    write_artifact(operator.folder, filename, content)
    size = path.stat().st_size
    safe_kind = kind if kind in ("markdown", "json", "text") else "markdown"
    await operator.emit_artifact(name=filename, path=str(path), kind=safe_kind)
    return {"path": str(path), "bytes": size}


# Per-kind required-field set. The planner must pass at least these fields
# in ``payload``; anything else is allowed but ignored. Mirrors the memory
# tables' minimum useful schemas — no field validation reaches the operator
# unless the user later confirms the proposal.
_PROPOSAL_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "fact":      ("fact",),
    "contact":   ("name",),
    "follow_up": ("what",),
    "decision":  ("decision",),
}


# strict_mode=False because ``payload`` is intentionally a free-form dict
# whose schema depends on ``kind`` (each kind validates differently inside
# the function body). The Agents SDK's strict mode rejects ``dict`` params
# because they emit ``additionalProperties`` in the JSON schema.
@function_tool(strict_mode=False)
async def propose_memory_update(
    ctx: RunContextWrapper[OperatorContext],
    kind: str,
    payload: dict,
    reason: str = "",
) -> dict:
    """Propose a memory update for the user to confirm at operation end.

    The planner uses this when a run surfaces something worth remembering for
    future operations — a person mentioned in sources, a fact worth keeping,
    a follow-up the operator should chase, a decision implied by the run.

    THIS TOOL DOES NOT WRITE TO MEMORY. It only queues a proposal. The user
    sees every proposal at completion and can confirm or dismiss each one;
    confirmed proposals are written by the backend via the existing
    memory_service. No silent writes.

    Args:
        kind: One of "fact" | "contact" | "follow_up" | "decision".
        payload: Kind-specific fields. Minimum required:
            - fact:      {"fact": str, optional "topic": str, "source": str}
            - contact:   {"name": str, optional "role", "company", "email", ...}
            - follow_up: {"what": str, optional "who": str, "due_iso": str}
            - decision:  {"decision": str, optional "context": str}
        reason: Short explanation of why this came up in the current run.

    Returns:
        {"id": str, "kind": str, "status": "proposed"} on success,
        {"error": str} on validation failure.
    """
    operator = ctx.context
    operator.note_tool("propose_memory_update")

    if kind not in ALLOWED_PROPOSAL_KINDS:
        msg = f"propose_memory_update rejected: kind {kind!r} not in {list(ALLOWED_PROPOSAL_KINDS)}"
        await operator.emit_error(msg)
        return {"error": msg}

    if not isinstance(payload, dict):
        msg = f"propose_memory_update rejected: payload must be a dict, got {type(payload).__name__}"
        await operator.emit_error(msg)
        return {"error": msg}

    required = _PROPOSAL_REQUIRED_FIELDS[kind]
    missing = [f for f in required if not str(payload.get(f, "")).strip()]
    if missing:
        msg = f"propose_memory_update({kind}) missing required fields: {missing}"
        await operator.emit_error(msg)
        return {"error": msg}

    proposal = await operator.emit_memory_proposal(
        kind=kind, payload=payload, reason=reason,
    )
    return {"id": proposal["id"], "kind": proposal["kind"], "status": proposal["status"]}


@function_tool
async def draft_gmail(
    ctx: RunContextWrapper[OperatorContext],
    to: str,
    subject: str,
    body: str,
) -> dict:
    """Create a Gmail draft. Drafts are NOT sent — they sit in the user's
    Drafts folder waiting for review.

    Use this when the command asks to draft / compose / write an email.
    Never use this to send. Per the operator's approval philosophy, drafts
    are internal artifacts; sends require explicit user approval through
    the renderer's email button (which is a separate path).

    Args:
        to: Recipient email address (must contain "@").
        subject: Draft subject line.
        body: Plain-text email body. Markdown is fine; Gmail renders it as text.

    Returns:
        {"draft_id": str, "compose_url": str, "to": str} on success.
        {"error": str} if Gmail isn't connected or the API call failed.
    """
    operator = ctx.context
    operator.note_tool("draft_gmail")
    await operator.emit_step(
        name="gmail_draft", status="running",
        detail=f"Creating Gmail draft to {to}…",
    )

    try:
        meta = await asyncio.to_thread(
            gmail_service.create_draft, to, subject, body,
        )
    except gmail_service.GmailError as exc:
        await operator.emit_step(name="gmail_draft", status="failed", detail=exc.detail)
        await operator.emit_error(f"draft_gmail failed: {exc.detail}")
        return {"error": exc.detail}
    except Exception as exc:  # noqa: BLE001
        msg = f"draft_gmail failed: {type(exc).__name__}: {exc}"
        await operator.emit_step(name="gmail_draft", status="failed", detail=msg)
        await operator.emit_error(msg)
        return {"error": str(exc)}

    # The draft is a real, durable artifact in Gmail. Surface it on the
    # artifacts panel with the compose_url so the renderer can show
    # "Open in Gmail" — same shape as a Drive folder link.
    await operator.emit_artifact(
        name=f"gmail_draft_{meta['draft_id'][:10]}",
        path=meta["compose_url"],
        kind="gmail_draft",
    )
    await operator.emit_step(
        name="gmail_draft", status="completed",
        detail=f"Draft saved to Gmail Drafts (to: {to}). Open in Gmail to review or send.",
    )
    return meta


# Exposed registry — what the planner sees. Order matters only for the
# system prompt's "available tools" list.
PLANNER_TOOLS = [
    web_research,
    write_sources_packet,
    write_audiobook_script,
    synthesize_audio,
    write_file,
    propose_memory_update,
    draft_gmail,
]


def tool_capability_summary() -> str:
    """Plain-text capability list rendered into the planner system prompt."""
    return "\n".join(
        f"- {t.name}: {(t.description or '').splitlines()[0] if t.description else ''}"
        for t in PLANNER_TOOLS
    )
