"""Per-operation context passed to every Operator tool.

The OpenAI Agents SDK lets you attach an arbitrary context object to a run
and access it inside any ``@function_tool`` via ``RunContextWrapper``. We
use that to give every tool:

  - the on-disk run folder (so tools know where to write artifacts),
  - the in-flight operation record (so tools can append steps + artifacts
    to the same log the renderer will see), and
  - the async ``emit`` function that pushes SSE timeline events to the
    renderer.

Holding state out-of-band like this keeps tool signatures clean — the
planner agent never has to remember to thread folder paths through every
tool call — while still letting tools produce real side effects on disk
and broadcast progress to the user.
"""

from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

# Proposal kinds the planner may suggest. Each maps 1:1 to a memory_service
# write API. Anything outside this set is rejected at tool-call time so a
# hallucinating planner can't invent a "category" that bypasses validation.
ALLOWED_PROPOSAL_KINDS: tuple[str, ...] = ("fact", "contact", "follow_up", "decision")

# An emit function pushes a single dict event into the SSE queue. Mirrors
# the operator_service.EmitFn alias so the two stay interchangeable.
EmitFn = Callable[[dict], Awaitable[None]]

# The operator context for the run currently executing on this async task.
# The Anthropic tool runner calls tools as plain functions (no per-call context
# argument like the old SDK's RunContextWrapper), so operator_service sets this
# contextvar at the start of every run/continue turn and the tools read it.
# Contextvars are task-local, so concurrent runs can't cross-contaminate.
_CURRENT_OPERATOR: ContextVar["OperatorContext | None"] = ContextVar(
    "ridian_current_operator", default=None
)


def set_current_operator(operator: "OperatorContext"):
    """Bind ``operator`` to the current async context. Returns the reset token."""
    return _CURRENT_OPERATOR.set(operator)


def current_operator() -> "OperatorContext":
    op = _CURRENT_OPERATOR.get()
    if op is None:
        raise RuntimeError(
            "No OperatorContext bound — operator tools must run inside an "
            "operation (operator_service.set_current_operator was not called)."
        )
    return op


@dataclass
class OperatorContext:
    """In-flight operation state shared with every tool call.

    ``folder``  — per-run output directory (``outputs/<timestamp>_<slug>/``).
                  Tools that write artifacts MUST stay inside this folder.
    ``record``  — the mutable operation log being built. Tools should append
                  step/artifact entries via the helpers below rather than
                  reaching in directly, so the SSE feed stays in sync.
    ``emit``    — async callable that puts a single event onto the SSE queue.
    """

    folder: Path
    record: dict
    emit: EmitFn

    # Cache of intermediate text artifacts the planner may want to pass to
    # the next tool without re-reading from disk. Optional; tools may write
    # directly to disk and return the path, or write here as a convenience.
    sources_packet_text: str = ""
    script_text: str = ""

    async def emit_step(self, *, name: str, status: str, detail: str = "") -> None:
        """Append a step to the record and broadcast it via SSE."""
        from datetime import datetime  # local import keeps module top tidy
        now = datetime.now().isoformat(timespec="seconds")
        step = next((s for s in self.record["steps"] if s["name"] == name), None)
        if step is None:
            step = {
                "name": name, "status": status,
                "started_at": now, "completed_at": "",
                "detail": detail,
            }
            self.record["steps"].append(step)
        else:
            step["status"] = status
            step["detail"] = detail or step.get("detail", "")
            if status in ("completed", "failed", "skipped"):
                step["completed_at"] = now
        await self.emit({"event": "step", "data": dict(step)})

    async def emit_artifact(self, *, name: str, path: str, kind: str) -> None:
        """Record an artifact + broadcast it (renderer adds to artifacts panel)."""
        artifact = {"name": name, "path": path, "kind": kind}
        self.record["artifacts"].append(artifact)
        await self.emit({"event": "artifact", "data": artifact})

    async def emit_error(self, message: str) -> None:
        self.record["errors"].append(message)
        await self.emit({"event": "error", "data": {"message": message}})

    def note_tool(self, name: str) -> None:
        """Add a tool name to record.tools_used (deduped at finalize)."""
        if name and name not in self.record["tools_used"]:
            self.record["tools_used"].append(name)

    async def emit_needs_input(
        self, *, question: str, context_hint: str = "",
        options: "list[dict] | None" = None,
    ) -> dict:
        """Record a missing-information request + broadcast it to the renderer.

        v1.7: when the planner is missing something only the user can supply
        (a recipient email, a choice between options), it asks instead of
        guessing or failing. The renderer shows an amber "Ridian needs one
        answer" card; the user replies in the command box and the 5-minute
        conversational-follow-up window carries the context forward.
        """
        entry = {
            "id": "need_" + uuid.uuid4().hex[:10],
            "question": (question or "").strip(),
            "context_hint": (context_hint or "").strip(),
            # v2.2: structured choices. Each option is
            #   {"label": str, "action": "submit"|"compose"|"disabled",
            #    "value": str (for submit)}. Empty ⇒ open-ended (free-text).
            # The tool that raises the question declares these; the UI renders
            # buttons for options or the composer for free-text.
            "options": [dict(o) for o in options] if options else [],
        }
        if "needs_input" not in self.record:
            self.record["needs_input"] = []
        self.record["needs_input"].append(entry)
        # v2: mark the run as paused-awaiting-the-user. operator_service uses
        # this to keep the operation session alive for a /continue instead of
        # finalizing, and to compute "awaiting_input" (not "partial") status.
        # Cleared at the start of each turn (run/continue).
        self.record["awaiting_input"] = True
        await self.emit({"event": "needs_input", "data": dict(entry)})
        return entry

    async def emit_memory_proposal(self, *, kind: str, payload: dict, reason: str = "") -> dict:
        """Record a proposed memory write + broadcast it to the renderer.

        Per the memo's approval philosophy: the planner only PROPOSES — it
        never writes to memory directly. The user confirms / dismisses each
        proposal in a single batch at operation completion through
        POST /operations/{id}/memory/commit.

        Returns the persisted proposal dict so the tool body can include
        the ``id`` in its tool-output for the planner's own bookkeeping.
        """
        proposal = {
            "id": "prop_" + uuid.uuid4().hex[:10],
            "kind": kind,
            "payload": payload,
            "reason": (reason or "").strip(),
            "status": "proposed",  # set to 'committed' or 'dismissed' later
        }
        if "proposed_memory_updates" not in self.record:
            self.record["proposed_memory_updates"] = []
        self.record["proposed_memory_updates"].append(proposal)
        await self.emit({"event": "memory_proposal", "data": dict(proposal)})
        return proposal
