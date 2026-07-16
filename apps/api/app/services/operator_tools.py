"""Operator tool registry — every tool produces a real side effect.

Per the redesign memo, §13: "Every tool in the registry must produce a side
effect (file written, draft created, upload completed) or it doesn't ship.
No tool whose output is 'here's a prompt.'"

These tools are decorated with ``@planner_tool`` — a thin wrapper around the
Anthropic SDK's ``beta_async_tool`` that (a) JSON-encodes each tool's dict
return for the model and (b) preserves the tool's signature/docstring so the
input schema is generated exactly as the OpenAI Agents SDK used to. Tools read
the active run's ``OperatorContext`` from a task-local contextvar
(``operator_context.current_operator``) set by operator_service, and emit SSE
timeline events + write artifacts through it.

Tools are intentionally narrow:
    web_research            — live web search, returns structured sources Markdown
    write_sources_packet    — writes sources_packet.md to disk
    write_audiobook_script  — generates a two-host script via a sub-agent
    synthesize_audio        — writes audiobook.mp3 (legacy; unexposed)
    write_file              — generic allowlisted artifact writer

The planner does NOT see web search or the script-writer sub-agent directly —
they're encapsulated inside ``web_research`` / ``build_research_packet`` /
``write_audiobook_script`` so the planner's tool list stays small and
business-shaped rather than infrastructure-shaped.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from pathlib import Path

from anthropic import beta_async_tool

import re as _re

from ..agents import load_prompt, model_supports_effort, research_model, script_model
from . import (
    browser_service,
    gmail_service,
    google_drive_service,
    google_workspace_service,
    memory_service,
    tts_service,
    url_fetch_service,
)
from .anthropic_runtime import (
    SEARCH_COST_USD,
    WEB_SEARCH_TOOL,
    RunBudgetExceeded,
    estimate_cost_usd,
    run_text_agent,
)
from .artifact_service import write_artifact
from .operator_context import (
    ALLOWED_PROPOSAL_KINDS,
    OperatorContext,
    current_operator,
)

log = logging.getLogger("ridian.operator.tools")


def planner_tool(fn):
    """Register an async tool with the Anthropic tool runner.

    The wrapped function keeps its exact signature and Google-style docstring
    (the SDK generates the input schema from both — same behavior as the old
    ``@function_tool``). Bodies keep returning dicts; the wrapper JSON-encodes
    them into the tool_result string the model reads.
    """
    @functools.wraps(fn)
    async def wrapper(**kwargs):
        result = await fn(**kwargs)
        if isinstance(result, str):
            return result
        return json.dumps(result, default=str)

    return beta_async_tool(wrapper)


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
    # General prose deliverable (letters, one-pagers, benefits docs, drafts).
    # The descriptive TITLE goes in the doc's H1 — the filename stays stable so
    # the allowlist can stay a fixed, auditable set rather than open-ended.
    "document.md",
})


# ---------------------------------------------------------------------------
# Internal sub-agents (not exposed to the planner directly)
# ---------------------------------------------------------------------------
# Each is a system prompt run one-shot via anthropic_runtime.run_text_agent;
# the research/packet ones attach the server-side web_search tool. The planner
# never sees web search directly — same encapsulation as before.

_RESEARCH_PROMPT = "operator_research_prompt.txt"
_SCRIPT_PROMPT = "operator_script_prompt.txt"
_PACKET_PROMPT = "operator_research_packet_prompt.txt"


# ---------------------------------------------------------------------------
# Source-lock grounding gate (deterministic — not model-dependent)
# ---------------------------------------------------------------------------
#
# When a command names a specific URL AND asks Ridian to use ONLY that source,
# the run is "locked" to it. If a read_url never succeeds (it failed, or the
# model skipped it), the build tools REFUSE to produce a deck/sheet/doc from
# other sources and raise the amber needs-input card instead. This mirrors
# draft_gmail's no-recipient guard: the model cannot build its way around a
# returned error, so the grounding guarantee is enforced in code, not prose.

_SOURCE_URL_RE = _re.compile(r"\bhttps?://[^\s<>\"']+", _re.IGNORECASE)
_SOURCE_DOMAIN_RE = _re.compile(
    r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>\"']*)?", _re.IGNORECASE
)
_SOURCE_GROUNDING_RE = _re.compile(
    r"\b(use only|only what'?s? on|only from|only this|based (?:only )?on|"
    r"from (?:this|these|that) (?:page|pages|site|url)|as the source|"
    r"grounded in|read (?:the )?(?:page|site|url))\b",
    _re.IGNORECASE,
)


def detect_source_lock(command: str) -> str:
    """Return the URL a command is locked to, or "" if it isn't source-locked.

    A run is source-locked only when the command BOTH names a URL/domain AND
    expresses grounding/exclusivity intent ("use only what's on this page",
    "based on <url>", "use <site> as the source", ...). Requiring both keeps a
    command that merely mentions a domain (e.g. an email address) from locking.
    """
    if not command:
        return ""
    m = _SOURCE_URL_RE.search(command) or _SOURCE_DOMAIN_RE.search(command)
    if not m or not _SOURCE_GROUNDING_RE.search(command):
        return ""
    url = m.group(0)
    return url if url.lower().startswith("http") else "https://" + url


async def _grounding_gate(operator: OperatorContext) -> dict | None:
    """Deterministic source-lock gate for the build tools.

    Returns None when building is allowed (the run isn't locked, or a read_url
    already succeeded and wrote source.md). Otherwise REFUSES: it surfaces the
    amber needs-input card ONCE and returns an error dict the planner must obey —
    it cannot build a deck/sheet/doc from other sources on a locked-but-
    ungrounded run.
    """
    rec = operator.record
    locked = rec.get("source_locked_url")
    # grounding_ok: a read_url (or an operator-pasted source) grounded the run.
    # grounding_override: on resume, the operator explicitly authorized general
    # web research, lifting the lock for this run (see continue_operation).
    if not locked or rec.get("grounding_ok") or rec.get("grounding_override"):
        return None
    if not rec.get("grounding_needs_input_emitted"):
        rec["grounding_needs_input_emitted"] = True
        await operator.emit_needs_input(
            question=(
                f"I couldn't ground this in the source you named ({locked}) — no "
                "readable text was obtained from it (the page may be empty, "
                "blocked, or JavaScript-rendered). I won't build from other "
                "sources without your say-so. How should I proceed?"
            ),
            context_hint=f"grounding failed for {locked}",
            options=[
                {"label": "Do general web research",
                 "value": "Do general web research instead", "action": "submit"},
                {"label": "I'll paste the page text",
                 "action": "compose", "placeholder": "Paste the page's text here…"},
                {"label": "Upload a PDF", "action": "upload"},
                {"label": "Headless render (coming soon)", "action": "disabled"},
            ],
        )
        await operator.emit_step(
            name="grounding_gate", status="skipped",
            detail=f"Refused to build — this run is not grounded in {locked}. "
                   "Awaiting your choice.",
        )
    return {
        "error": (
            f"BLOCKED: this run is locked to {locked} as the source, but grounding "
            "failed (read_url did not produce source.md). Do NOT build from other "
            "sources and do NOT retry this tool — a needs-input question has been "
            "raised for the operator. Stop after this."
        ),
        "reason": "grounding_required",
    }


def _search_lock_gate(operator: OperatorContext) -> dict | None:
    """Deterministic search exclusion for source-locked runs.

    "Use only what's on this page" means the web is off-limits — even AFTER a
    successful read (grounding_ok does NOT unlock search; supplementing a
    locked source from the open web would violate the lock). The only key is
    ``grounding_override``: the operator explicitly authorizing general web
    research on resume. Enforced in code so the model cannot be talked (or
    talk itself) into searching on a locked run — the prompt rule alone is
    obedience, not a guarantee.
    """
    rec = operator.record
    if not rec.get("source_locked_url") or rec.get("grounding_override"):
        return None
    return {
        "error": (
            f"BLOCKED: this run is locked to {rec.get('source_locked_url')} as its "
            "only source — web search is disabled in code for locked runs. Use "
            "read_url on the named source (or the already-provided source text). "
            "If the source can't be read, the build tools will raise the "
            "needs-input question; do NOT retry search."
        ),
        "reason": "search_locked",
    }


# ---------------------------------------------------------------------------
# Deliverable-intent gate (deterministic — no documents for small talk)
# ---------------------------------------------------------------------------
#
# "How do you feel?" once produced a 6-slide deck and a spreadsheet because the
# planner prompt's deliverables-first identity filled the vacuum with profile
# content. Prompt rules alone have been overridden before (grounding,
# recipient), so this is enforced in code: the build tools refuse on a run
# whose command never asked for a deliverable, and the planner is told to just
# answer conversationally. False positives are harmless (an open gate doesn't
# force building); false negatives get a conversational reply asking what to
# build — the correct failure direction for ambiguous input.

_DELIVERABLE_VERB_RE = _re.compile(
    r"\b(build|create|make|draft|write|compose|prepare|generate|produce|"
    r"assemble|put together|whip up|research|sweep|summari[sz]e|turn\b.{0,30}\binto)\b",
    _re.IGNORECASE,
)
_DELIVERABLE_NOUN_RE = _re.compile(
    r"\b(deck|slides?|presentation|pitch|spread ?sheet|sheet|tracker|"
    r"documents?|docs?|one-? ?pagers?|letters?|briefs?|briefings?|packets?|"
    r"reports?|memos?|outlines?|proposals?|emails?|drafts?|summar(?:y|ies)|"
    r"audiobooks?|scripts?|comparison)\b",
    _re.IGNORECASE,
)


def detect_deliverable_intent(command: str) -> bool:
    """True when the command plausibly asks for a deliverable.

    Permissive by design — a deliverable VERB (build/draft/research/...) OR a
    deliverable NOUN (deck/sheet/doc/email/...) counts. Only clearly
    conversational input (greetings, opinions, chit-chat: no verb AND no noun)
    closes the gate.
    """
    if not command:
        return False
    return bool(_DELIVERABLE_VERB_RE.search(command)
                or _DELIVERABLE_NOUN_RE.search(command))


def _deliverable_gate(operator: OperatorContext) -> dict | None:
    """Refuse to build on a run whose command never asked for a deliverable.

    Quiet refusal (no step, no needs-input): a conversational message deserves
    a conversational answer, not an interrogation. Returns None when building
    is allowed; missing key defaults to allowed (legacy/resumed records).
    """
    if operator.record.get("deliverable_intent", True):
        return None
    return {
        "error": (
            "BLOCKED: the operator's message is conversational — it did not ask "
            "for a deliverable. Do NOT build a deck, spreadsheet, or document. "
            "Answer the operator directly and warmly in your final receipt, with "
            "no further tool calls."
        ),
        "reason": "no_deliverable_request",
    }


# ---------------------------------------------------------------------------
# Recipient provenance gate (deterministic — draft_gmail never guesses an email)
# ---------------------------------------------------------------------------
#
# draft_gmail must never create a real Gmail draft to an address the model
# invented. A recipient is allowed ONLY if it matches a known contact in memory
# OR an address the operator explicitly typed (captured verbatim from the
# command, and from a resume answer via continue_operation). Otherwise the tool
# refuses BEFORE drafting and raises the needs-input question itself — the same
# refuse-and-ask contract as _grounding_gate, never guess-then-ask.

_EMAIL_RE = _re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def extract_emails(text: str) -> list[str]:
    """Lowercased email addresses in free text, order-preserving and deduped.
    Used to capture operator-typed recipients from the command / resume answer."""
    seen: list[str] = []
    for m in _EMAIL_RE.findall(text or ""):
        e = m.lower()
        if e not in seen:
            seen.append(e)
    return seen


def _address_only(to: str) -> str:
    """Extract the bare address from ``to``, handling 'Name <addr>' forms."""
    m = _EMAIL_RE.search(str(to or ""))
    return m.group(0).lower() if m else ""


def _known_emails(operator: OperatorContext) -> set[str]:
    """Every email Ridian can verify: known contacts + operator-typed addresses."""
    emails: set[str] = set()
    try:
        for c in memory_service.list_contacts():
            e = (c.get("email") or "").strip().lower()
            if e:
                emails.add(e)
    except Exception:  # noqa: BLE001
        pass
    for e in (operator.record.get("user_provided_emails") or []):
        e = str(e).strip().lower()
        if e:
            emails.add(e)
    return emails


def _recipient_is_known(operator: OperatorContext, to: str) -> bool:
    addr = _address_only(to)
    return bool(addr) and addr in _known_emails(operator)


async def _require_known_recipient(operator: OperatorContext, to: str) -> dict | None:
    """Refuse a recipient the model may have invented. Returns None if the
    address is verifiable (known contact or operator-typed); otherwise emits the
    needs-input question ONCE and returns a refusal the planner must obey."""
    if _recipient_is_known(operator, to):
        return None
    rec = operator.record
    addr = _address_only(to) or (to or "").strip()
    asked = rec.setdefault("recipient_asked", [])
    if addr not in asked:
        asked.append(addr)
        await operator.emit_needs_input(
            question=(
                f'I don\'t have a verified email for "{addr}". I won\'t draft to an '
                "address I might have guessed. What's the correct email address "
                "(or which contact on file should I use)?"
            ),
            context_hint="unverified email recipient",
        )
        await operator.emit_step(
            name="gmail_draft", status="skipped",
            detail=f"Refused to draft to an unverified address ({addr}). "
                   "Awaiting the real address.",
        )
    return {
        "error": (
            f"BLOCKED: '{addr}' is not a known contact and was not provided by the "
            "operator — it may be invented. Do NOT create this draft and do NOT "
            "retry with a guessed address. A needs-input question has been raised; "
            "wait for the operator to supply the real address."
        ),
        "reason": "recipient_unverified",
    }


# Prepended to research output when the sub-agent ran ZERO live web searches:
# the content is model memory, and the artifact itself must say so — a code
# guarantee, not a prompt rule (the model would happily present it as live).
_UNGROUNDED_BANNER = (
    "> ⚠ **UNGROUNDED** — built without any live web searches; this content "
    "comes from model memory, not current sources. Verify before use.\n\n"
)


def _effective_research_model(operator: OperatorContext) -> str:
    """The composer's per-run override (allowlisted at intake by
    operator_service._sanitize_research_model), else the Settings/env
    default. Research sub-agents only — the planner never reads this."""
    return operator.record.get("research_model_override") or research_model()


def _effective_script_model(operator: OperatorContext) -> str:
    """Per-run Script selector override, else the Settings/env default
    (which itself falls back to the planner model, preserving the script
    writer's historical behavior)."""
    return operator.record.get("script_model_override") or script_model()


def _effective_effort(operator: OperatorContext) -> str:
    """Per-run effort override for SUB-AGENT calls only ("" = API default,
    which is 'high'). The planner's effort is deliberately not per-run
    switchable. run_text_agent omits it for models that reject it (Haiku)."""
    return operator.record.get("effort_override") or ""


def _effort_note(operator: OperatorContext, model: str) -> str:
    """Human-readable effort description for plan/step lines — names what
    will ACTUALLY be sent, including the Haiku omission."""
    eff = _effective_effort(operator)
    if not eff:
        return "effort: default"
    if not model_supports_effort(model):
        return f"effort: default ({eff} requested — n/a on Haiku, omitted)"
    return f"effort: {eff}"


# ---------------------------------------------------------------------------
# Research plan gate — approve BEFORE any search spend (v3 governed research)
# ---------------------------------------------------------------------------

# Deterministic estimate constants — no model call builds the plan (instant,
# free, can't hallucinate the numbers). Per-search and per-token rates live in
# anthropic_runtime (SEARCH_COST_USD / estimate_cost_usd — one shared math for
# the plan, the ceiling, the reconciliation, and failure forensics); dynamic-
# filtering code execution is free alongside web search.
#
# The cost band and time band are PROVISIONAL — calibrated on ZERO
# measured-token runs (token logging ships with this change; the 2026-07-15
# live run's shape was 8 searches / 27 tool rounds / 9m04s, but its tokens
# were not recorded). Every run now prints a plan-vs-actual reconciliation
# from real usage, which self-corrects the promise; recalibrate these
# constants from the logged tokens_in/tokens_out once a few runs exist.
_RESEARCH_COST_ESTIMATE = "$0.40–$0.80"
_RESEARCH_EST_HIGH_USD = 0.80   # numeric high end of the band above — keep in step
_RESEARCH_TIME_ESTIMATE = "typically 4–9 minutes"

# Button values for the plan question. operator_service._apply_research_answer
# matches these (plus plain-language equivalents) on resume.
RESEARCH_PLAN_PROCEED = "Proceed with the research plan"
RESEARCH_PLAN_CANCEL = "Cancel the research"


def _fmt_elapsed(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _reconciliation(res, model: str) -> str:
    """Plan-vs-actual, computed from REAL usage at the EFFECTIVE model's dated
    rates (the old version priced every run at Sonnet rates even when the
    override ran Opus/Fable) — every research run self-audits, so the operator
    sees exactly what the approval bought."""
    actual_cost = estimate_cost_usd(model, res.tokens_in, res.tokens_out,
                                    searches=res.searches)
    max_uses = WEB_SEARCH_TOOL.get("max_uses", 8)
    return (f"Plan: up to {max_uses} searches, est ≈{_RESEARCH_COST_ESTIMATE} — "
            f"actual: {res.searches} searches, {_fmt_elapsed(res.elapsed_seconds)}, "
            f"≈${actual_cost:.2f}")


# ---------------------------------------------------------------------------
# Hard per-run cost ceiling (v3.2) — layer 1 of the dollar fence
# ---------------------------------------------------------------------------
# record["cost_ceiling_usd"] is snapshotted at intake (operator_service) and
# record["spend_usd"] accumulates planner turns + sub-agent calls + FAILED
# calls' partials. Layer 1 (here): billable tools refuse to start once the run
# is at/over the fence. Layer 2 (anthropic_runtime): the live mid-stream guard
# aborts a call in flight the moment observed spend crosses it.


def _cost_ceiling_gate(operator: OperatorContext) -> dict | None:
    """Refuse to start a billable call on a run already at its dollar ceiling.
    Returns None when spend is under the fence (or there is no fence)."""
    rec = operator.record
    ceiling = rec.get("cost_ceiling_usd")
    if ceiling is None:
        return None
    spent = float(rec.get("spend_usd", 0.0) or 0.0)
    if spent < ceiling:
        return None
    return {
        "error": (
            f"BLOCKED: this run has reached its cost ceiling — ≈${spent:.2f} "
            f"spent of the ${ceiling:.2f} limit. Do NOT call billable tools "
            "again and do NOT retry. Tell the operator the ceiling stopped "
            "the run (it can be raised in Settings, or set to 'off')."),
        "reason": "cost_ceiling",
    }


def _add_spend(operator: OperatorContext, model: str, res) -> None:
    """Fold a completed sub-agent call into the run's dollar ledger."""
    operator.record["spend_usd"] = round(
        float(operator.record.get("spend_usd", 0.0) or 0.0)
        + estimate_cost_usd(model, res.tokens_in, res.tokens_out,
                            searches=res.searches), 4)


def _add_partial_spend(operator: OperatorContext, exc: Exception) -> str:
    """Fold a FAILED call's pre-death spend — attached by anthropic_runtime as
    ``ridian_partial`` — into the run's ledger. Failed runs still bill for the
    searches and tokens that ran; returns a sentence for the failed step so
    that money is STATED, never silently swallowed ("" when unknown)."""
    partial = getattr(exc, "ridian_partial", None)
    if not isinstance(partial, dict):
        return ""
    cost = float(partial.get("cost_usd", 0.0) or 0.0)
    operator.record["spend_usd"] = round(
        float(operator.record.get("spend_usd", 0.0) or 0.0) + cost, 4)
    return (f" Partial spend before the failure ≈${cost:.2f} "
            f"({int(partial.get('searches', 0) or 0)} searches) — billed.")


def _ceiling_kwargs(operator: OperatorContext) -> dict:
    """The run's fence, passed into run_text_agent so the live mid-stream
    guard sees prior spend too — layer 2 of the same ceiling."""
    return {
        "cost_ceiling": operator.record.get("cost_ceiling_usd"),
        "spent_usd": float(operator.record.get("spend_usd", 0.0) or 0.0),
    }


async def _research_plan_gate(
    operator: OperatorContext, topic: str, time_window: str,
) -> dict | None:
    """Approve-before-spend for live web research.

    Research runs cost real money (per-search billing + model tokens) and
    minutes of foreground time, so the FIRST research call on an operation
    presents a deterministic plan through the standard needs_input pause and
    waits. The gate passes only on record["research_approved"] — set
    EXCLUSIVELY by operator_service._apply_research_answer from the
    operator's own resume answer, so the planner can never talk itself (or
    be talked) past it. Positioned before run_text_agent is ever called:
    zero API spend on the unapproved and declined paths, by construction.
    """
    rec = operator.record
    if rec.get("research_approved"):
        if rec.get("research_plan_asked") and not rec.get("research_plan_resolved"):
            rec["research_plan_resolved"] = True
            await operator.emit_step(
                name="research_plan", status="completed",
                detail="Approved by the operator — starting research.",
            )
        return None
    if rec.get("research_declined"):
        if not rec.get("research_plan_resolved"):
            rec["research_plan_resolved"] = True
            await operator.emit_step(
                name="research_plan", status="skipped",
                detail="Declined by the operator — no searches were run.",
            )
        return {
            "error": ("The operator DECLINED the research plan. Do NOT run web "
                      "research and do NOT retry or work around it; acknowledge "
                      "the cancellation briefly in your receipt."),
            "reason": "research_declined",
        }
    if not rec.get("research_plan_asked"):
        rec["research_plan_asked"] = True
        max_uses = WEB_SEARCH_TOOL.get("max_uses", 8)
        search_fees = f"${max_uses * SEARCH_COST_USD:.2f}"
        plan_model = _effective_research_model(operator)
        # Every approval names the run's dollar fence — and when the estimate
        # band straddles it, says so BEFORE the spend: cancelling here costs
        # nothing, discovering the ceiling mid-run costs the partial bill.
        ceiling = rec.get("cost_ceiling_usd")
        spent = float(rec.get("spend_usd", 0.0) or 0.0)
        if ceiling is None:
            ceiling_note = "Run ceiling: off."
        else:
            ceiling_note = (f"Run ceiling: ${ceiling:.2f} "
                            f"(≈${spent:.2f} already spent this run).")
            if spent + _RESEARCH_EST_HIGH_USD > ceiling:
                ceiling_note += (
                    " Heads-up: this research will likely hit the ceiling and "
                    "be stopped mid-run with the partial spend billed — "
                    "cancelling now costs nothing; the ceiling can be raised "
                    "in Settings.")
        await operator.emit_needs_input(
            question=(
                f"Research plan — approve before I spend anything. "
                f"Topic: {topic}. Window: {time_window}. "
                f"Up to {max_uses} live web searches ({search_fees} in search fees) "
                f"on {plan_model} ({_effort_note(operator, plan_model)}), "
                f"{_RESEARCH_TIME_ESTIMATE}, ≈{_RESEARCH_COST_ESTIMATE} total. "
                f"{ceiling_note} Proceed?"
            ),
            context_hint="research plan approval — no spend until you answer",
            options=[
                {"label": "Proceed", "action": "submit", "value": RESEARCH_PLAN_PROCEED},
                {"label": "Cancel", "action": "submit", "value": RESEARCH_PLAN_CANCEL},
            ],
        )
        await operator.emit_step(
            name="research_plan", status="running",
            detail=(f"Awaiting your approval — up to {max_uses} searches on "
                    f"{_effective_research_model(operator)}, ≈{_RESEARCH_COST_ESTIMATE}."),
        )
    return {
        "error": ("BLOCKED: the research plan needs the operator's approval before "
                  "any search spend. A needs-input question has been raised; wait "
                  "for the operator's answer. Do NOT retry and do NOT research "
                  "another way."),
        "reason": "research_plan_pending",
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@planner_tool
async def web_research(
    topic: str,
    time_window: str = "last 30 days",
    depth: str = "strategic",
) -> dict:
    """Run live web research on ``topic`` and return a finished sources packet.

    Uses Anthropic's server-side web search through an internal sub-agent. The
    returned ``sources_md`` is a Markdown sources packet ready to be passed
    to ``write_sources_packet`` or ``write_audiobook_script``. Source URLs
    are cited; confidence flags are included.

    Args:
        topic: The subject to research.
        time_window: How recent to skew (default "last 30 days").
        depth: One of "quick" | "strategic" | "deep" — controls source count.

    Returns:
        {"sources_md": str, "sources_count": int, "ungrounded": bool,
        "searches_billed": int, "elapsed_seconds": float,
        "reconciliation": str} — include the ``reconciliation`` line VERBATIM
        in your final receipt (plan-vs-actual self-audit). ``ungrounded`` is
        true when ZERO live searches ran, meaning the content came from model
        memory; report that honestly, never as live research. The FIRST
        research call on an operation pauses for the operator's plan approval
        (a code gate): on {"reason": "research_plan_pending"} WAIT for the
        operator's answer; on {"reason": "research_declined"} acknowledge
        and stop.
    """
    operator = current_operator()
    operator.note_tool("web_research")
    # Cost gate sits BEFORE the plan gate: never ask the operator to approve
    # a plan the dollar fence would refuse anyway.
    block = _search_lock_gate(operator) or _cost_ceiling_gate(operator)
    if block:
        return block
    block = await _research_plan_gate(operator, topic, time_window)
    if block:
        return block
    await operator.emit_step(
        name="research", status="running",
        detail=f"Searching the live web — topic: {topic} ({time_window}, {depth}).",
    )

    max_uses = WEB_SEARCH_TOOL.get("max_uses", 8)

    async def _progress(phase: str, n: int) -> None:
        if phase == "search":
            detail = f"Searching {n} of up to {max_uses}…"
        elif phase == "filter":
            detail = f"Filtering results… ({n} searches so far)"
        else:
            detail = f"Reading results and writing… ({n} searches so far)"
        await operator.emit_step(name="research", status="running", detail=detail)

    prompt = (
        f"Operator command topic: {topic}\n"
        f"Time window: {time_window}\n"
        f"Depth: {depth}\n\n"
        "Produce the sources packet now."
    )
    try:
        res = await run_text_agent(
            load_prompt(_RESEARCH_PROMPT), prompt, use_web_search=True,
            return_stats=True, model=_effective_research_model(operator),
            on_progress=_progress, effort=_effective_effort(operator) or None,
            **_ceiling_kwargs(operator),
        )
        sources_md = res.text.strip()
        _add_spend(operator, _effective_research_model(operator), res)
    except Exception as exc:
        partial_note = _add_partial_spend(operator, exc)
        await operator.emit_step(
            name="research", status="failed",
            detail=f"Web research failed: {type(exc).__name__}: {exc}."
                   + partial_note)
        await operator.emit_error(f"web_research failed: {exc}")
        out = {"sources_md": "", "sources_count": 0, "error": str(exc)}
        if isinstance(exc, RunBudgetExceeded):
            out["reason"] = "cost_ceiling"
            out["error"] += (" Do NOT retry any billable tool; tell the "
                             "operator the cost ceiling stopped the run.")
        return out

    # Verification: count "### " headings (one per source per prompt spec).
    import re
    count = len(re.findall(r"^###\s+\S", sources_md, flags=re.MULTILINE))
    # v3.3: the review email lists what the run covered without opening the
    # artifact — persist the source titles (capped; they ride the snapshot).
    operator.record["source_titles"] = [
        t.strip() for t in
        re.findall(r"^###\s+(.+)$", sources_md, flags=re.MULTILINE)
    ][:20]

    # Zero live searches means every "source" came from model memory — the
    # packet must say so, in the artifact and to the planner, not present
    # itself as live research.
    ungrounded = res.searches == 0
    if ungrounded and sources_md:
        sources_md = _UNGROUNDED_BANNER + sources_md
        operator.record["ungrounded_research"] = True

    operator.sources_packet_text = sources_md
    operator.record["sources_count"] = count
    recon = _reconciliation(res, _effective_research_model(operator))
    # Persisted for the review email's cost detail line — the step detail
    # below also carries it, but parsing step strings client-side is fragile.
    operator.record["reconciliation"] = recon
    detail = f"{count} sources gathered ({recon})."
    if ungrounded:
        detail = (f"{count} sources gathered — ⚠ UNGROUNDED (0 live web searches; "
                  f"model memory). {recon}.")
    await operator.emit_step(name="research", status="completed", detail=detail)
    return {"sources_md": sources_md, "sources_count": count,
            "ungrounded": ungrounded, "searches_billed": res.searches,
            "elapsed_seconds": round(res.elapsed_seconds, 1),
            "reconciliation": recon}


@planner_tool
async def write_sources_packet(
    content: str,
) -> dict:
    """Write a sources packet to ``sources_packet.md`` in the run folder.

    Use after ``web_research``. The planner can pass the ``sources_md`` from
    research directly into this tool's ``content`` argument.

    Returns:
        {"path": str, "bytes": int}
    """
    operator = current_operator()
    operator.note_tool("write_file")
    block = _deliverable_gate(operator)
    if block:
        return block
    if not content or not content.strip():
        await operator.emit_error("write_sources_packet called with empty content; skipping.")
        return {"path": "", "bytes": 0, "error": "empty content"}

    path = operator.folder / "sources_packet.md"
    write_artifact(operator.folder, "sources_packet.md", content)
    size = path.stat().st_size
    operator.sources_packet_text = content
    await operator.emit_artifact(name="sources_packet.md", path=str(path), kind="markdown")
    return {"path": str(path), "bytes": size}


@planner_tool
async def build_research_packet(
    topic: str,
    time_window: str = "last 30 days",
) -> dict:
    """Build a paste-ready NotebookLM research packet and write it to disk.

    This is the deliverable for "build a research packet" / "give me sources I
    can drop into NotebookLM" commands. It runs live web research through an
    internal sub-agent and writes ONE clean Markdown file,
    ``research_packet.md``, formatted to paste into NotebookLM as a single
    source: a title + date, an "Audio Overview focus" framing line, then each
    source as ``## Source Title`` + plain URL + a tight 3-5 sentence summary in
    Ridian's own words (no long quotes — paste-clean).

    Ryan pastes the file into a NotebookLM notebook and generates the Audio
    Overview himself. There is NO NotebookLM API call and NO browser
    automation here — the file is the deliverable. (To open the NotebookLM
    site afterward, the planner uses the separate ``open_browser`` tool, and
    only when the command explicitly asks for it.)

    Args:
        topic: The subject to research (e.g. "newest in agentic AI frameworks").
        time_window: How recent to skew (default "last 30 days"; pass e.g.
            "this week" / "last 7 days" when the command says so).

    Returns:
        {"path": str, "bytes": int, "sources_count": int, "ungrounded": bool,
        "searches_billed": int, "elapsed_seconds": float,
        "reconciliation": str} on success — include the ``reconciliation``
        line VERBATIM in your final receipt (plan-vs-actual self-audit).
        ``ungrounded`` true means ZERO live searches ran and the packet is
        model memory (the file carries a warning banner; say so in the
        receipt). {"path": "", "bytes": 0, "sources_count": 0, "error": str}
        on failure. The FIRST research call on an operation pauses for the
        operator's plan approval (a code gate): on
        {"reason": "research_plan_pending"} WAIT for the operator's answer;
        on {"reason": "research_declined"} acknowledge and stop.
    """
    operator = current_operator()
    operator.note_tool("build_research_packet")
    # Cost gate before the plan gate — never ask approval for a plan the
    # dollar fence would refuse anyway.
    block = (_deliverable_gate(operator) or _search_lock_gate(operator)
             or _cost_ceiling_gate(operator))
    if block:
        return block
    if not topic or not topic.strip():
        await operator.emit_error("build_research_packet called without a topic; skipping.")
        return {"path": "", "bytes": 0, "sources_count": 0, "error": "no topic"}
    block = await _research_plan_gate(operator, topic, time_window)
    if block:
        return block

    await operator.emit_step(
        name="research_packet", status="running",
        detail=f"Researching for a NotebookLM packet — topic: {topic} ({time_window}).",
    )

    max_uses = WEB_SEARCH_TOOL.get("max_uses", 8)

    async def _progress(phase: str, n: int) -> None:
        if phase == "search":
            detail = f"Searching {n} of up to {max_uses}…"
        elif phase == "filter":
            detail = f"Filtering results… ({n} searches so far)"
        else:
            detail = f"Reading results and writing… ({n} searches so far)"
        await operator.emit_step(name="research_packet", status="running", detail=detail)

    prompt = (
        f"Topic: {topic}\n"
        f"Time window: {time_window}\n\n"
        "Produce the research packet body now (focus line + sources)."
    )
    try:
        res = await run_text_agent(
            load_prompt(_PACKET_PROMPT), prompt, use_web_search=True,
            return_stats=True, model=_effective_research_model(operator),
            on_progress=_progress, effort=_effective_effort(operator) or None,
            **_ceiling_kwargs(operator),
        )
        body = res.text.strip()
        _add_spend(operator, _effective_research_model(operator), res)
    except Exception as exc:  # noqa: BLE001
        partial_note = _add_partial_spend(operator, exc)
        await operator.emit_step(
            name="research_packet", status="failed",
            detail=f"Research failed: {type(exc).__name__}: {exc}."
                   + partial_note)
        await operator.emit_error(f"build_research_packet failed: {exc}")
        out = {"path": "", "bytes": 0, "sources_count": 0, "error": str(exc)}
        if isinstance(exc, RunBudgetExceeded):
            out["reason"] = "cost_ceiling"
            out["error"] += (" Do NOT retry any billable tool; tell the "
                             "operator the cost ceiling stopped the run.")
        return out

    if not body:
        await operator.emit_step(name="research_packet", status="failed",
                                 detail="Research returned no sources; nothing to write.")
        await operator.emit_error("build_research_packet produced an empty packet; not writing.")
        return {"path": "", "bytes": 0, "sources_count": 0, "error": "empty packet"}

    # Count "## " headings — one per source per the packet prompt spec.
    import re
    from datetime import datetime
    count = len(re.findall(r"^##\s+\S", body, flags=re.MULTILINE))
    # v3.3: the review email lists what the packet covered without opening
    # it — persist the source titles (capped; they ride the snapshot).
    operator.record["source_titles"] = [
        t.strip() for t in re.findall(r"^##\s+(.+)$", body, flags=re.MULTILINE)
    ][:20]

    # Stamp the title + date deterministically (never trust the LLM for "today").
    title = topic.strip().rstrip(".")
    if len(title) > 80:
        title = title[:77].rstrip() + "…"
    dateline = datetime.now().strftime("%B %d, %Y")
    # Zero live searches → the packet is model memory, and it must say so at
    # the top, before any content the user might paste into NotebookLM.
    ungrounded = res.searches == 0
    banner = _UNGROUNDED_BANNER if ungrounded else ""
    if ungrounded:
        operator.record["ungrounded_research"] = True
    packet = (
        f"# Research Packet — {title}\n\n"
        f"**Prepared by Ridian · {dateline}**\n\n{banner}{body}\n"
    )

    path = operator.folder / "research_packet.md"
    write_artifact(operator.folder, "research_packet.md", packet)
    size = path.stat().st_size
    operator.sources_packet_text = packet
    operator.record["sources_count"] = count
    # This packet is a LOCAL file for pasting into NotebookLM — uploading it to
    # Drive adds no value and a Drive failure would be pure noise. Flag the run
    # so auto_upload_drive skips cleanly. Only this tool sets the flag, so
    # email/sheet/deck runs still auto-file to Drive normally.
    operator.record["skip_drive_upload"] = True
    await operator.emit_artifact(name="research_packet.md", path=str(path), kind="markdown")
    recon = _reconciliation(res, _effective_research_model(operator))
    # Persisted for the review email's cost detail line — the step detail
    # below also carries it, but parsing step strings client-side is fragile.
    operator.record["reconciliation"] = recon
    detail = (f"Packet ready — {count} sources ({recon}). Paste research_packet.md "
              f"into NotebookLM as one source, then generate the Audio Overview.")
    if ungrounded:
        detail = (f"Packet ready — {count} sources, ⚠ UNGROUNDED (0 live web "
                  f"searches; content is model memory — verify before use). {recon}.")
    await operator.emit_step(name="research_packet", status="completed", detail=detail)
    return {"path": str(path), "bytes": size, "sources_count": count,
            "ungrounded": ungrounded, "searches_billed": res.searches,
            "elapsed_seconds": round(res.elapsed_seconds, 1),
            "reconciliation": recon}


@planner_tool
async def read_url(
    url: str,
) -> dict:
    """Fetch a SPECIFIC web page the operator named and return its real text.

    Use this to GROUND a deliverable in an actual page (e.g. a chamber's
    membership / benefits pages) instead of general-knowledge guesses. It
    fetches the page server-side behind an SSRF guard (http/https only, no
    internal/loopback addresses, size + timeout caps), extracts the main
    readable text, and saves it to ``source.md`` in the run folder for
    provenance.

    Call this FIRST when the command names a URL or says "use <site> as the
    source" / "from this page" / "based on <url>", THEN build the doc / deck /
    sheet from the returned ``text``. Ground STRICTLY in that text: if a detail
    (a tier, price, benefit, date, name) isn't in it, omit it — never invent.
    Fetches only the page you pass (no crawling).

    Args:
        url: A full http(s) URL to a specific public web page.

    Returns:
        {"url", "title", "text", "chars", "truncated", "source_file"} on
        success, {"error": str} on a blocked/failed fetch (do NOT fabricate
        content — report the failure instead).
    """
    operator = current_operator()
    operator.note_tool("read_url")
    if not url or not url.strip():
        await operator.emit_error("read_url called without a URL; skipping.")
        return {"error": "no url"}

    await operator.emit_step(name="read_url", status="running",
                             detail=f"Fetching {url.strip()} …")
    try:
        result = await asyncio.to_thread(url_fetch_service.fetch_and_extract, url)
    except url_fetch_service.ReadUrlError as exc:
        operator.record["grounding_failed"] = True
        await operator.emit_step(name="read_url", status="failed", detail=exc.detail)
        await operator.emit_error(f"read_url failed: {exc.detail}")
        return {"error": exc.detail}
    except Exception as exc:  # noqa: BLE001
        operator.record["grounding_failed"] = True
        msg = f"read_url failed: {type(exc).__name__}: {exc}"
        await operator.emit_step(name="read_url", status="failed", detail=msg)
        await operator.emit_error(msg)
        return {"error": str(exc)}

    text = (result.get("text") or "").strip()
    if not text:
        operator.record["grounding_failed"] = True
        detail = "Fetched the page but couldn't extract readable text from it."
        await operator.emit_step(name="read_url", status="failed", detail=detail)
        return {"error": detail, "url": result.get("url", "")}

    # Provenance: accumulate every fetched source into source.md so the operator
    # can see exactly what Ridian grounded on. Append on repeat reads; emit the
    # artifact row only the first time.
    src_path = operator.folder / "source.md"
    first = not src_path.exists()
    block = (f"## {result.get('title') or result.get('url')}\n\n"
             f"{result.get('url')}\n\n{text}\n\n---\n\n")
    prior = src_path.read_text(encoding="utf-8") if src_path.exists() else "# Fetched sources\n\n"
    write_artifact(operator.folder, "source.md", prior + block)
    # A real page was read: the source-lock grounding gate is now satisfied.
    operator.record["grounding_ok"] = True
    if first:
        await operator.emit_artifact(name="source.md", path=str(src_path), kind="markdown")

    await operator.emit_step(
        name="read_url", status="completed",
        detail=f"Read {result.get('url')} — {result.get('chars')} chars"
               + (" (truncated)" if result.get("truncated") else "") + ".",
    )
    return {
        "url": result.get("url"),
        "title": result.get("title"),
        "text": text,
        "chars": result.get("chars"),
        "truncated": result.get("truncated"),
        "source_file": "source.md",
    }


@planner_tool
async def write_audiobook_script(
    sources_md: str,
    target_minutes: int = 15,
) -> dict:
    """Generate a NotebookLM-style two-host script from a sources packet.

    Writes ``script.md`` to the run folder. The script uses ``**Host A**:``
    and ``**Host B**:`` markers the synthesize_audio tool relies on.

    Returns:
        {"path": str, "bytes": int, "estimated_seconds": int}
    """
    operator = current_operator()
    operator.note_tool("write_audiobook_script")
    # The script sub-agent is a billable model call — the run's dollar fence
    # covers it like every other spend.
    block = _cost_ceiling_gate(operator)
    if block:
        return block
    if not sources_md or not sources_md.strip():
        await operator.emit_error("write_audiobook_script called without sources; skipping.")
        return {"path": "", "bytes": 0, "estimated_seconds": 0, "error": "no sources"}

    await operator.emit_step(name="script", status="running",
                             detail=f"Writing two-host script (~{target_minutes} min target).")

    prompt = (
        f"Sources packet:\n\n{sources_md}\n\n"
        f"Target spoken runtime: ~{target_minutes} minutes.\n"
        "Produce the audiobook script now."
    )
    try:
        res = await run_text_agent(
            load_prompt(_SCRIPT_PROMPT), prompt,
            model=_effective_script_model(operator),
            effort=_effective_effort(operator) or None,
            return_stats=True, **_ceiling_kwargs(operator),
        )
        script_md = res.text.strip()
        _add_spend(operator, _effective_script_model(operator), res)
    except Exception as exc:
        partial_note = _add_partial_spend(operator, exc)
        await operator.emit_step(
            name="script", status="failed",
            detail=f"Script generation failed: {type(exc).__name__}: {exc}."
                   + partial_note)
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


@planner_tool
async def synthesize_audio(
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
    operator = current_operator()
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


@planner_tool
async def write_file(
    filename: str,
    content: str,
    kind: str = "markdown",
) -> dict:
    """Write an allowlisted file into the run folder.

    Use this when the planner wants to save an artifact that doesn't have a
    dedicated tool (e.g., ``brief.md`` for a research-only command, or
    ``document.md`` for a general prose deliverable like a letter, one-pager,
    or benefits doc). ``filename`` MUST be one of the allowlisted names — the
    planner cannot write arbitrary paths or escape the run folder. Do NOT put
    the document's title in the filename; use the stable allowlisted name and
    put the title in the document's first-line ``# H1`` heading instead.

    Returns:
        {"path": str, "bytes": int}
    """
    operator = current_operator()
    operator.note_tool("write_file")
    block = _deliverable_gate(operator)
    if block:
        return block
    gate = await _grounding_gate(operator)
    if gate:
        return gate
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

# v1.6: facts must be FACTS. "Recent developments in AGI highlight
# advancements in capabilities" is a platitude, not a fact, and it wastes
# the operator's memory + attention. The bar: named entity or number,
# a source, and none of the known filler phrases.
_VAGUE_FACT_RE = _re.compile(
    r"\b(highlights?|landscape|advancements? in capabilities|industry perspectives?"
    r"|ethical considerations?|rapidly evolving|stay (?:tuned|informed)"
    r"|in general|various stakeholders|continues? to evolve)\b",
    _re.IGNORECASE,
)


def _fact_quality_error(payload: dict) -> str | None:
    """Return a rejection reason for a low-quality fact, or None if it passes.

    A storable fact needs:
      1. >= 20 characters of actual claim,
      2. a non-empty source (URL or publication name),
      3. a named entity (capitalized word beyond position 0) or a number, and
      4. no platitude filler phrases.
    The error strings double as teaching signals — the planner reads them
    and retries with a real fact or skips.
    """
    fact = str(payload.get("fact", "") or "").strip()
    source = str(payload.get("source", "") or "").strip()
    if len(fact) < 20:
        return "fact rejected: too short — state a concrete claim (who/what/when)."
    if not source:
        return ("fact rejected: facts require a source (URL or publication name). "
                "If you can't cite it, don't store it.")
    words = fact.split()
    has_digit = any(ch.isdigit() for ch in fact)
    has_entity = any(w[:1].isupper() for w in words[1:])
    if not (has_digit or has_entity):
        return ("fact rejected: no named entity or number found. Name the company, "
                "product, person, date, or figure.")
    if _VAGUE_FACT_RE.search(fact):
        return ("fact rejected: reads like a platitude ('highlights advancements', "
                "'evolving landscape'...). State the specific thing that happened.")
    return None


# strict_mode=False because ``payload`` is intentionally a free-form dict
# whose schema depends on ``kind`` (each kind validates differently inside
# the function body). The Agents SDK's strict mode rejects ``dict`` params
# because they emit ``additionalProperties`` in the JSON schema.
@planner_tool
async def propose_memory_update(
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
    operator = current_operator()
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

    # v1.6: facts get a quality gate. The rejection text is returned to the
    # planner (not surfaced as a user-facing error) so it can retry with a
    # real fact or — correctly — skip proposing anything.
    if kind == "fact":
        quality_err = _fact_quality_error(payload)
        if quality_err:
            return {"error": quality_err}

    proposal = await operator.emit_memory_proposal(
        kind=kind, payload=payload, reason=reason,
    )
    return {"id": proposal["id"], "kind": proposal["kind"], "status": proposal["status"]}


@planner_tool
async def draft_gmail(
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

    RECIPIENT RULE: ``to`` must be a KNOWN contact's email or an address the
    operator explicitly typed. Ridian never invents an address — if the
    recipient can't be verified, this tool refuses and asks first (it does NOT
    draft to a guessed address).

    Args:
        to: Recipient email — a known contact or an address the operator typed.
        subject: Draft subject line.
        body: Plain-text email body. Markdown is fine; Gmail renders it as text.

    Returns:
        {"draft_id": str, "compose_url": str, "to": str} on success.
        {"error": str} if the recipient is unverified, Gmail isn't connected, or
        the API call failed.
    """
    return await _draft_gmail(current_operator(), to, subject, body)


async def _draft_gmail(operator: OperatorContext, to: str, subject: str, body: str) -> dict:
    """Testable core of draft_gmail (no SDK ctx wrapper)."""
    operator.note_tool("draft_gmail")

    # v1.7: missing recipient is a PLANNER mistake, not a user-facing failure.
    # Return the correction quietly (no red error wall) so the planner can
    # call request_missing_info instead — per the operator's explicit rule:
    # "If no recipient email is available, do not call gmail_draft."
    if not to or "@" not in to:
        return {"error": (
            "no valid recipient email. Do NOT retry draft_gmail and do NOT "
            "invent an address — call request_missing_info to ask the user "
            "which email to use."
        )}

    # Provenance gate: refuse a recipient the model may have invented — allow
    # ONLY a known contact or an operator-typed address. Refuse-and-ask BEFORE
    # drafting, never guess-then-ask. (Mirrors _grounding_gate.)
    recipient_block = await _require_known_recipient(operator, to)
    if recipient_block:
        return recipient_block

    # v1.7 circuit breaker: once Gmail fails with a CONFIGURATION error
    # (API not enabled / not connected / missing scope), every further
    # draft in this operation will fail identically. Short-circuit instead
    # of hammering the API six times and printing six red rows.
    config_block = getattr(operator, "_gmail_config_error", None)
    if config_block:
        return {"error": (
            f"Gmail is still unavailable in this run ({config_block}) — do NOT "
            "retry. Summarize the drafts you could not create in your receipt."
        )}

    await operator.emit_step(
        name="gmail_draft", status="running",
        detail=f"Creating Gmail draft to {to}…",
    )

    try:
        meta = await asyncio.to_thread(
            gmail_service.create_draft, to, subject, body,
        )
    except gmail_service.GmailError as exc:
        is_config = any(s in exc.detail for s in (
            "isn't enabled", "not connected", "permission is missing",
        ))
        if is_config:
            operator._gmail_config_error = exc.detail  # arm the breaker
        await operator.emit_step(name="gmail_draft", status="failed", detail=exc.detail)
        await operator.emit_error(f"draft_gmail failed: {exc.detail}")
        return {"error": exc.detail + (
            " This is a configuration issue — do NOT retry in this run."
            if is_config else ""
        )}
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


@planner_tool
async def auto_upload_drive(
) -> dict:
    """File the current run's artifacts in the operator's Google Drive.

    Approval-free: it's the operator's OWN Drive, and the app uses the narrow
    drive.file scope (it can only see files/folders it created itself). Call
    this tool ONCE per run, AFTER the artifact tools succeed, BEFORE the final
    summary message. Skip the call if Google Drive isn't connected.

    Returns:
        {"drive_path": str, "drive_folder_url": str, "uploaded_files": [str]}
        on success.
        {"error": str, "reason": str} on failure (planner should mention this
        in the final summary but NOT retry — the manual Upload button is the
        re-try path).
    """
    operator = current_operator()
    operator.note_tool("auto_upload_drive")

    # Some runs produce LOCAL-only deliverables (e.g. build_research_packet's
    # NotebookLM packet) that set this flag. Skip Drive cleanly — a skip, not a
    # failure — so the timeline doesn't show "auto_upload_drive failed" noise.
    if operator.record.get("skip_drive_upload"):
        msg = "Skipped Drive upload — this run's deliverable is a local file (NotebookLM packet)."
        await operator.emit_step(name="drive_upload", status="skipped", detail=msg)
        return {"skipped": True, "reason": "local_only_deliverable"}

    await operator.emit_step(
        name="drive_upload", status="running",
        detail="Uploading the run to Google Drive (operator's own Drive, drive.file scope).",
    )

    if not operator.record.get("artifacts"):
        msg = "Nothing to upload yet — no artifacts on this run."
        await operator.emit_step(name="drive_upload", status="skipped", detail=msg)
        return {"error": msg, "reason": "no_artifacts"}

    try:
        result = await asyncio.to_thread(
            google_drive_service.upload_artifact_folder,
            str(operator.folder),
        )
    except google_drive_service.GoogleDriveError as exc:
        await operator.emit_step(name="drive_upload", status="failed", detail=exc.detail)
        await operator.emit_error(f"auto_upload_drive failed: {exc.detail}")
        return {"error": exc.detail, "reason": "drive_error"}
    except Exception as exc:  # noqa: BLE001
        msg = f"auto_upload_drive failed: {type(exc).__name__}: {exc}"
        await operator.emit_step(name="drive_upload", status="failed", detail=msg)
        await operator.emit_error(msg)
        return {"error": str(exc), "reason": "unexpected"}

    if result.get("status") == "skipped":
        # No local allowlisted file to file (e.g. an email-draft-only or
        # Google-Sheet-only run whose deliverables already live in Gmail/Drive).
        # Clean grey skip — no empty Drive folder was created, no red error.
        msg = "Skipped Drive upload — this run produced no local file to file in Drive."
        await operator.emit_step(name="drive_upload", status="skipped", detail=msg)
        return {"skipped": True, "reason": result.get("reason", "no_files")}

    drive_path = result.get("drive_path") or result.get("drive_folder_name") or "Drive"
    drive_url = result.get("drive_folder_url") or ""
    uploaded = result.get("uploaded_files") or []

    if drive_url:
        # Surface the Drive folder as a real artifact row with an "Open in Drive"
        # link. Same shape as the gmail_draft artifact — the renderer recognizes
        # http(s) paths on non-file artifacts and renders an external anchor.
        await operator.emit_artifact(
            name=f"drive_folder ({len(uploaded)} files)",
            path=drive_url,
            kind="drive_folder",
        )
    await operator.emit_step(
        name="drive_upload", status="completed",
        detail=f"Uploaded {len(uploaded)} files to {drive_path}.",
    )
    return {
        "drive_path": drive_path,
        "drive_folder_url": drive_url,
        "uploaded_files": uploaded,
    }


@planner_tool
async def create_spreadsheet(
    title: str,
    headers: list[str],
    rows: list[list[str]],
) -> dict:
    """Create a LIVE Google Sheet in the operator's Drive — a real deliverable.

    Use this whenever the deliverable is comparative or structured data:
    competitor comparisons, pricing trackers, prospect lists, expense
    summaries, content calendars. A spreadsheet the operator can sort,
    share, and present beats a Markdown table every time.

    Cells use USER_ENTERED parsing, so:
      - numbers ("1500", "12.5%") become real numbers,
      - formulas ("=C2-B2", "=AVERAGE(D2:D9)") compute live.
    Header row is bolded + frozen automatically. The file lands in
    Ridian Operator / Spreadsheets in Drive.

    Args:
        title: Spreadsheet name, specific and dated when useful
               (e.g. "Gulf Coast AI Consultants — Pricing Comparison, Jun 2026").
        headers: Column names, first row.
        rows: Data rows — each a list of cell strings aligned to headers.

    Returns:
        {"spreadsheet_id": str, "url": str} on success, {"error": str} on failure.
    """
    operator = current_operator()
    operator.note_tool("create_spreadsheet")
    block = _deliverable_gate(operator)
    if block:
        return block
    gate = await _grounding_gate(operator)
    if gate:
        return gate
    await operator.emit_step(
        name="spreadsheet", status="running",
        detail=f"Building Google Sheet: {title} ({len(rows)} rows).",
    )
    try:
        meta = await asyncio.to_thread(
            google_workspace_service.create_spreadsheet, title, headers, rows,
        )
    except google_workspace_service.GoogleWorkspaceError as exc:
        await operator.emit_step(name="spreadsheet", status="failed", detail=exc.detail)
        await operator.emit_error(f"create_spreadsheet failed: {exc.detail}")
        return {"error": exc.detail}
    except Exception as exc:  # noqa: BLE001
        msg = f"create_spreadsheet failed: {type(exc).__name__}: {exc}"
        await operator.emit_step(name="spreadsheet", status="failed", detail=msg)
        await operator.emit_error(msg)
        return {"error": str(exc)}

    await operator.emit_artifact(
        name=title[:80], path=meta["url"], kind="spreadsheet",
    )
    await operator.emit_step(
        name="spreadsheet", status="completed",
        detail=f"Live Google Sheet ready: {len(headers)} columns × {len(rows)} rows.",
    )
    return meta


# strict_mode stays ON: parallel lists (titles + bullets) keep the JSON
# schema strict-compatible, unlike a list-of-dicts slides param.
@planner_tool
async def create_slide_deck(
    title: str,
    slide_titles: list[str],
    slide_bullets: list[list[str]],
) -> dict:
    """Create a LIVE Google Slides deck in the operator's Drive.

    Use this when the deliverable is a presentation: pitches, workshop
    outlines, chamber talks, client proposals-as-decks. The operator opens
    it in Slides, tweaks, and presents — no copy-paste from Markdown.

    Slide 0 with an empty bullets list renders as a big centered title
    slide. Every other slide is title + real disc bullets. Keep bullets
    short (max ~12 words each, 3-5 per slide) — these are slides, not docs.

    Args:
        title: Deck file name.
        slide_titles: One title per slide, in order.
        slide_bullets: One list of bullet strings per slide, aligned with
                       slide_titles ([] for the title slide).

    Returns:
        {"presentation_id": str, "url": str} on success, {"error": str} on failure.
    """
    operator = current_operator()
    operator.note_tool("create_slide_deck")
    block = _deliverable_gate(operator)
    if block:
        return block
    gate = await _grounding_gate(operator)
    if gate:
        return gate
    if len(slide_titles) != len(slide_bullets):
        msg = (f"create_slide_deck rejected: slide_titles ({len(slide_titles)}) and "
               f"slide_bullets ({len(slide_bullets)}) must be the same length.")
        await operator.emit_error(msg)
        return {"error": msg}

    await operator.emit_step(
        name="deck", status="running",
        detail=f"Building Google Slides deck: {title} ({len(slide_titles)} slides).",
    )
    try:
        meta = await asyncio.to_thread(
            google_workspace_service.create_presentation,
            title, slide_titles, slide_bullets,
        )
    except google_workspace_service.GoogleWorkspaceError as exc:
        await operator.emit_step(name="deck", status="failed", detail=exc.detail)
        await operator.emit_error(f"create_slide_deck failed: {exc.detail}")
        return {"error": exc.detail}
    except Exception as exc:  # noqa: BLE001
        msg = f"create_slide_deck failed: {type(exc).__name__}: {exc}"
        await operator.emit_step(name="deck", status="failed", detail=msg)
        await operator.emit_error(msg)
        return {"error": str(exc)}

    await operator.emit_artifact(
        name=title[:80], path=meta["url"], kind="slides",
    )
    await operator.emit_step(
        name="deck", status="completed",
        detail=f"Live Slides deck ready: {len(slide_titles)} slides.",
    )
    return meta


# Exposed registry — what the planner sees. Order matters only for the
# system prompt's "available tools" list.
#
# Audio tools (synthesize_audio, write_audiobook_script) are intentionally
# absent in v1.4+. NotebookLM produces better audiobooks for free and OpenAI
# TTS billing made the audiobook path the most expensive per-run capability.
# The tool functions stay defined in this file for backward compat and to
# allow easy re-introduction; they're just not exposed to the planner.
@planner_tool
async def request_missing_info(
    question: str,
    context_hint: str = "",
) -> dict:
    """Ask the operator for information only they can supply, then stop that
    thread of work gracefully.

    Use when the command needs something memory doesn't have: a recipient
    email address, a choice between candidates, a missing detail. NEVER
    guess and NEVER invent. Consolidate everything you're missing into ONE
    clear question (e.g. "I need email addresses for: Sarah Chen (Chamber)
    and Ada's Kids — which should I use?").

    The renderer shows the question prominently; the user answers in the
    command box and the conversational follow-up context carries your prior
    work forward — so finish whatever parts you CAN complete first.

    Args:
        question: One clear, specific question for the operator.
        context_hint: Optional one-liner about which task is blocked.

    Returns:
        {"status": "awaiting_user", "id": str}
    """
    operator = current_operator()
    operator.note_tool("request_missing_info")
    entry = await operator.emit_needs_input(question=question, context_hint=context_hint)
    await operator.emit_step(
        name="needs_input", status="completed",
        detail=f"Waiting on you: {question}",
    )
    return {"status": "awaiting_user", "id": entry["id"]}


# strict_mode=False: like propose_memory_update, the payload shape depends
# on kind, so the JSON schema can't be strict.
@planner_tool
async def save_memory(
    kind: str,
    payload: dict,
) -> dict:
    """Save something to memory DIRECTLY — only when the user explicitly
    commanded it ("add a contact…", "remember that…", "save a follow-up…").

    The user's explicit command IS the approval, so no confirmation panel.
    For anything YOU noticed on your own during a run, use
    propose_memory_update instead — agent-initiated learning always needs
    the user's sign-off.

    Args:
        kind: "contact" | "fact" | "follow_up" | "decision".
        payload: Same shapes as propose_memory_update. For facts the user
            states personally, source may be omitted (defaults to "operator").

    Returns:
        {"id": str, "kind": str, "status": "saved"} or {"error": str}.
    """
    operator = current_operator()
    operator.note_tool("save_memory")

    if kind not in ALLOWED_PROPOSAL_KINDS:
        return {"error": f"save_memory rejected: kind {kind!r} not in {list(ALLOWED_PROPOSAL_KINDS)}"}
    if not isinstance(payload, dict):
        return {"error": "save_memory rejected: payload must be a dict"}
    required = _PROPOSAL_REQUIRED_FIELDS[kind]
    missing = [f for f in required if not str(payload.get(f, "")).strip()]
    if missing:
        return {"error": f"save_memory({kind}) missing required fields: {missing}"}

    try:
        if kind == "contact":
            entry = memory_service.add_contact({
                "name": str(payload.get("name", "") or ""),
                "role": str(payload.get("role", "") or ""),
                "company": str(payload.get("company", "") or ""),
                "email": str(payload.get("email", "") or ""),
                "phone": str(payload.get("phone", "") or ""),
                "notes": str(payload.get("notes", "") or ""),
                "last_contact_iso": str(payload.get("last_contact_iso", "") or ""),
            })
            summary = entry.get("name", "")
        elif kind == "fact":
            # User-stated facts don't need a citation — the user IS the source.
            entry = memory_service.add_fact({
                "topic": str(payload.get("topic", "") or ""),
                "fact": str(payload.get("fact", "") or ""),
                "source": str(payload.get("source", "") or "operator"),
            })
            summary = entry.get("fact", "")[:60]
        elif kind == "follow_up":
            entry = memory_service.add_follow_up({
                "what": str(payload.get("what", "") or ""),
                "who": str(payload.get("who", "") or ""),
                "due_iso": str(payload.get("due_iso", "") or ""),
                "status": "open",
                "source_run": str(payload.get("source_run", "") or ""),
            })
            summary = entry.get("what", "")[:60]
        else:  # decision
            entry = memory_service.add_decision({
                "decision": str(payload.get("decision", "") or ""),
                "context": str(payload.get("context", "") or ""),
                "date_iso": str(payload.get("date_iso", "") or ""),
            })
            summary = entry.get("decision", "")[:60]
    except Exception as exc:  # noqa: BLE001
        msg = f"save_memory failed: {type(exc).__name__}: {exc}"
        await operator.emit_error(msg)
        return {"error": str(exc)}

    await operator.emit_step(
        name="memory", status="completed",
        detail=f"Saved {kind.replace('_', '-')}: {summary}",
    )
    return {"id": entry.get("id", ""), "kind": kind, "status": "saved"}


@planner_tool
async def open_browser(
    target: str,
    browser: str = "chrome",
) -> dict:
    """Open a website in the operator's browser — a real action on their machine.

    Use when the command says open / pull up / take me to / launch a site,
    or when it's the natural finish to a task ("research X and open
    NotebookLM so I can drop the sources in"; "open the deck you just made").

    Args:
        target: A known site by name — "NotebookLM", "Drive", "Gmail",
            "Calendar", "Sheets", "Slides", "ChatGPT", "Claude",
            "Perplexity", "Gemini", "GitHub", "LinkedIn" — OR a full
            http(s) URL (e.g. a Google Sheet/Slides/Drive URL from an
            earlier step in this run). Only http/https opens.
        browser: "chrome" (default) to prefer Google Chrome; anything
            else opens the operator's default browser.

    Returns:
        {"url": str, "browser_used": str, "opened": bool} or {"error": str}.
    """
    operator = current_operator()
    operator.note_tool("open_browser")
    await operator.emit_step(
        name="browser", status="running",
        detail=f"Opening {target} in {browser}…",
    )
    try:
        result = await asyncio.to_thread(browser_service.open_url, target, browser)
    except browser_service.BrowserError as exc:
        await operator.emit_step(name="browser", status="failed", detail=exc.detail)
        await operator.emit_error(f"open_browser failed: {exc.detail}")
        return {"error": exc.detail}
    except Exception as exc:  # noqa: BLE001
        msg = f"open_browser failed: {type(exc).__name__}: {exc}"
        await operator.emit_step(name="browser", status="failed", detail=msg)
        await operator.emit_error(msg)
        return {"error": str(exc)}

    # Surface as an external artifact so there's a clickable fallback even if
    # the OS launch somehow didn't focus a window.
    await operator.emit_artifact(
        name=result["url"], path=result["url"], kind="browser",
    )
    await operator.emit_step(
        name="browser", status="completed",
        detail=f"Opened {result['url']} in {result['browser_used']} browser.",
    )
    return result


PLANNER_TOOLS = [
    web_research,
    read_url,
    write_sources_packet,
    build_research_packet,
    write_file,
    propose_memory_update,
    save_memory,
    draft_gmail,
    request_missing_info,
    auto_upload_drive,
    create_spreadsheet,
    create_slide_deck,
    open_browser,
]


def tool_capability_summary() -> str:
    """Plain-text capability list rendered into the planner system prompt."""
    lines = []
    for t in PLANNER_TOOLS:
        desc = (t.to_dict().get("description") or "").strip()
        lines.append(f"- {t.name}: {desc.splitlines()[0] if desc else ''}")
    return "\n".join(lines)
