"""Shared Anthropic runtime — the client + a one-shot text-agent helper.

This replaces the OpenAI Agents SDK's ``Agent`` + ``Runner.run`` pattern for
every single-shot agent in Ridian (the operator's research/script/packet
sub-agents and the legacy workflow specialists). The operator's main planner
loop lives in ``operator_service`` on the beta Tool Runner; this module covers
everything simpler.

Web search uses Anthropic's server-side ``web_search_20260209`` tool — no
client-side execution loop; results (with citations) are folded into the
response by the API. Long server-tool turns can stop with
``stop_reason == "pause_turn"``; per the documented pattern we re-send the
conversation to resume, capped so a wedged turn can't loop forever.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime

import httpx
from anthropic import AsyncAnthropic

from ..agents import default_model, model_supports_effort

log = logging.getLogger("ridian.anthropic")

# max_uses bounds per-search billing on a single sub-agent turn: the research
# prompts ask for 5-12 sources, which a handful of searches covers. NOTE:
# web_search_20260209 runs dynamic-filtering code execution under the hood —
# those rounds also appear as server_tool_use blocks but are NOT searches and
# are NOT billed (code execution is free alongside web search).
WEB_SEARCH_TOOL: dict = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}

_MAX_PAUSE_RESTARTS = 4

# Wall-clock ceiling for ONE text-agent turn (all pause segments combined),
# enforced via asyncio.wait_for — independent of byte flow. The observed
# research band is 4-9 minutes; 12 gives headroom without ever allowing an
# unbounded run.
_MAX_TURN_SECONDS = 720.0

# Per-read timeout for STREAMED turns only. Dynamic filtering runs quiet
# stretches with NO bytes on the wire — two of three 2026-07-15 baseline
# research runs died at ~300s of stream silence (ReadTimeout) while the server
# was still working, so the client-wide 300s default is wrong for streams.
# 660s sits under the 720s wall clock so the wall clock stays the single
# binding bound: this adds ZERO spend or duration headroom (search cap,
# max_tokens, and wall clock unchanged) — it only removes a false-failure
# mode that charged for searches and returned nothing.
_STREAM_READ_TIMEOUT = httpx.Timeout(660.0, connect=5.0)


class RunDeadlineExceeded(RuntimeError):
    """A text-agent turn exceeded the wall-clock ceiling."""


class ResearchBudgetExceeded(RuntimeError):
    """A streamed turn attempted more billed searches than the approved cap."""


class RunBudgetExceeded(RuntimeError):
    """A run crossed the operator's hard dollar ceiling."""


# Per-MTok (input, output) rates for run-spend accounting, verified 2026-07-16
# against the live pricing docs. Sonnet 5 is introductory pricing through
# 2026-08-31 (then $3/$15) — recheck these when that flips. Prefix-matched so
# dated ids (claude-haiku-4-5-20251001) resolve. Unknown models price at the
# top (Fable) tier so the cost fence never under-counts.
_MODEL_RATES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_TOP_TIER_RATES = (10.0, 50.0)

# $10 per 1,000 searches. A search that itself errors is not billed, but
# searches that completed before a run died ARE — failed runs still cost money.
SEARCH_COST_USD = 0.01


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int,
                      *, searches: int = 0) -> float:
    """Dollar cost of one model call from its usage numbers — the shared math
    behind the run cost ceiling, the plan reconciliation line, and failed-run
    forensics, so all three always agree."""
    rate_in, rate_out = next(
        (r for prefix, r in _MODEL_RATES_PER_MTOK.items()
         if (model or "").startswith(prefix)),
        _TOP_TIER_RATES,
    )
    return (searches * SEARCH_COST_USD
            + tokens_in / 1e6 * rate_in
            + tokens_out / 1e6 * rate_out)

# Per-request bound + single retry: healthy turns finish well under two
# minutes (a full 8-search research turn measured ~80s), while the SDK
# defaults (up to 450s/attempt x 3 attempts for our max_tokens) let one
# wedged request grind ~23 minutes with the run showing nothing. 300s keeps
# 3-4x headroom over the slowest healthy turn and turns a hung request into
# an honest, reported failure within ~10 minutes worst case.
_REQUEST_TIMEOUT_SECONDS = 300.0
_MAX_REQUEST_RETRIES = 1

_client: AsyncAnthropic | None = None
_client_key: str | None = None


def get_client() -> AsyncAnthropic:
    """Process-wide async client. Reads ANTHROPIC_API_KEY from the environment
    (settings_service.apply_to_environment mirrors the Settings value there
    before every run). Re-constructed automatically if the key changes in
    Settings, so a mid-session key swap takes effect on the next run."""
    global _client, _client_key
    import os
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if _client is None or _client_key != key:
        _client = AsyncAnthropic(
            timeout=_REQUEST_TIMEOUT_SECONDS, max_retries=_MAX_REQUEST_RETRIES,
        )
        _client_key = key
    return _client


def date_line() -> str:
    """The 'today' line injected into every agent context — evaluated from the
    live clock at CALL time, never a constant. Without it the model anchors
    "this week" to its training data (a live run on 2026-07-13 narrated "the
    current date context appears to be around mid-December 2025"). Weekday
    included so relative phrases like "this week" resolve unambiguously."""
    now = datetime.now()
    return f"Today's date: {now.strftime('%A, %B %d, %Y')} ({now.date().isoformat()})."


@dataclass
class TextAgentResult:
    """Final text plus run forensics, for callers that must verify grounding
    and reconcile plan vs actual."""
    text: str
    searches: int            # BILLED web searches (usage-derived; named-block fallback)
    restarts: int
    tool_rounds: int = 0     # every server_tool_use block, incl. dynamic filtering
    elapsed_seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


def _final_text(blocks) -> str:
    """Only the text AFTER the last non-text block — final-output semantics.

    With server-side web search the model narrates between searches ("I'll
    search the live web…", "I hit the search limit…") as text blocks
    interleaved with ``server_tool_use`` / ``web_search_tool_result`` blocks.
    Joining ALL text blocks leaked that narration into artifacts; the final
    synthesis is everything after the last tool block. A turn with no tool
    blocks keeps all its text (pause_turn can split one answer in segments).
    """
    last_tool = -1
    for i, block in enumerate(blocks):
        if getattr(block, "type", "") != "text":
            last_tool = i
    return "".join(
        block.text for block in blocks[last_tool + 1:]
        if getattr(block, "type", "") == "text"
    ).strip()


async def run_text_agent(
    system: str,
    user_input,
    *,
    use_web_search: bool = False,
    max_tokens: int = 16000,
    return_stats: bool = False,
    model: str | None = None,
    on_progress=None,
    effort: str | None = None,
    cost_ceiling: float | None = None,
    spent_usd: float = 0.0,
):
    """One-shot agent: system prompt + user input → final text.

    ``user_input`` is a plain string or an Anthropic content-block list
    (for multimodal input). With ``use_web_search`` the server-side web search
    tool is attached and ``pause_turn`` continuations are handled. With
    ``return_stats`` returns a :class:`TextAgentResult` (text + search count)
    instead of a plain string, so research callers can flag zero-search runs
    as ungrounded. ``model`` overrides default_model() for this call (the
    research sub-agents pass research_model()).

    ``on_progress`` is an optional ``async (phase, n)`` callback that makes
    the request STREAM instead of run silent. Phases: ``("search", n)`` as
    each BILLED web search starts, ``("filter", n)`` for the dynamic-filtering
    code-execution rounds the 20260209 tool runs under the hood (NOT searches,
    not billed), and ``("writing", n)`` when text follows a search — ``n`` is
    always the search count so far, carried across pause_turn restarts.

    Spend bounds, enforced HERE regardless of server behavior: billed searches
    can never exceed WEB_SEARCH_TOOL's max_uses (resumed segments get only the
    remaining budget; a hostile stream is aborted with
    :class:`ResearchBudgetExceeded`), and the whole turn is capped at
    ``_MAX_TURN_SECONDS`` wall-clock (:class:`RunDeadlineExceeded`) —
    independent of byte flow, which the per-read request timeout is not.

    ``cost_ceiling`` / ``spent_usd`` wire the operator's hard dollar ceiling
    into this call: the live guard aborts with :class:`RunBudgetExceeded` the
    moment (run spend so far + this call's observed searches and tokens)
    crosses the ceiling. The live figure is a slight UNDERestimate — server-
    tool round input lands in usage at segment boundaries — so the guard trips
    on the conservative number and the caller's reconciliation prints the
    exact one. Any exception leaving this function carries a
    ``ridian_partial`` dict (searches/tokens/cost observed before the death)
    and logs an ``anthropic.run_failed`` line: failed runs still bill for what
    ran, so the ledger must show it even when the completion log never fires.
    """
    client = get_client()
    effective_model = model or default_model()
    kwargs: dict = {
        "model": effective_model,
        "max_tokens": max_tokens,
        "system": f"{date_line()}\n\n{system}",
        "messages": [{"role": "user", "content": user_input}],
    }
    # output_config.effort is a real GA request param (no token budgets exist
    # behind the levels on current models — the API takes the level verbatim).
    # Haiku 4.5 rejects it, so it's omitted there rather than 400ing the run.
    if effort and model_supports_effort(effective_model):
        kwargs["output_config"] = {"effort": effort}
    search_cap: int | None = None
    if use_web_search:
        kwargs["tools"] = [dict(WEB_SEARCH_TOOL)]   # copy — max_uses shrinks on resume
        search_cap = int(WEB_SEARCH_TOOL.get("max_uses", 8))

    def _search_count(resp) -> int:
        """Named-block fallback for billed searches — filter rounds excluded."""
        return sum(
            1 for b in resp.content
            if getattr(b, "type", "") == "server_tool_use"
            and getattr(b, "name", "") == "web_search"
        )

    def _round_count(resp) -> int:
        return sum(1 for b in resp.content if getattr(b, "type", "") == "server_tool_use")

    def _billed_searches(resp) -> int | None:
        """The authoritative billed count from usage; None when unavailable."""
        stu = getattr(getattr(resp, "usage", None), "server_tool_use", None)
        n = getattr(stu, "web_search_requests", None)
        return int(n) if isinstance(n, int) else None

    def _tokens(resp) -> tuple[int, int]:
        u = getattr(resp, "usage", None)
        return (int(getattr(u, "input_tokens", 0) or 0),
                int(getattr(u, "output_tokens", 0) or 0))

    started = time.monotonic()

    async def _bounded(coro):
        """Run one API round trip under the turn's wall-clock ceiling."""
        remaining = _MAX_TURN_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            coro.close()
            raise RunDeadlineExceeded(
                f"turn exceeded the {int(_MAX_TURN_SECONDS)}s wall-clock ceiling")
        try:
            return await asyncio.wait_for(coro, timeout=remaining)
        except asyncio.TimeoutError:
            raise RunDeadlineExceeded(
                f"turn exceeded the {int(_MAX_TURN_SECONDS)}s wall-clock ceiling"
            ) from None

    searches = 0        # billed, from completed segments' usage
    rounds = 0
    tokens_in = 0       # completed segments' usage
    tokens_out = 0
    restarts = 0
    live_searches = 0   # stream-observed billed proxy, across segments
    seg_in = 0          # current stream segment, live (message_start/_delta)
    seg_out = 0

    def _spend_now() -> float:
        """Run spend if this call died right now: prior run spend + completed
        segments' usage + the current stream's live counters. Conservative —
        never more than the final bill."""
        return spent_usd + estimate_cost_usd(
            effective_model, tokens_in + seg_in, tokens_out + seg_out,
            searches=max(live_searches, searches))

    def _ceiling_check() -> None:
        """The operator's dollar fence, checked as spend becomes observable —
        per search fired and per usage update mid-stream, and before starting
        another paid segment."""
        if cost_ceiling is not None and _spend_now() > cost_ceiling:
            raise RunBudgetExceeded(
                f"stopped at ≈${_spend_now():.2f} of the ${cost_ceiling:.2f} "
                f"run cost ceiling — spend up to this point is billed")

    def _absorb(resp) -> None:
        """Fold one completed segment's usage into the run totals."""
        nonlocal searches, rounds, tokens_in, tokens_out, seg_in, seg_out
        billed = _billed_searches(resp)
        searches += billed if billed is not None else _search_count(resp)
        rounds += _round_count(resp)
        t_in, t_out = _tokens(resp)
        tokens_in += t_in
        tokens_out += t_out
        seg_in = seg_out = 0   # now counted in the totals above

    async def _streamed():
        nonlocal live_searches, seg_in, seg_out
        seg_in = seg_out = 0
        async with client.messages.stream(
                **kwargs, timeout=_STREAM_READ_TIMEOUT) as stream:
            async for event in stream:
                etype = getattr(event, "type", "")
                if etype == "message_start":
                    u = getattr(getattr(event, "message", None), "usage", None)
                    seg_in = int(getattr(u, "input_tokens", 0) or 0)
                    _ceiling_check()
                elif etype == "message_delta":
                    u = getattr(event, "usage", None)
                    seg_out = max(seg_out, int(getattr(u, "output_tokens", 0) or 0))
                    # server-tool turns re-report input as internal rounds land
                    seg_in = max(seg_in, int(getattr(u, "input_tokens", 0) or 0))
                    _ceiling_check()
                if etype != "content_block_start":
                    continue
                block = getattr(event, "content_block", None)
                btype = getattr(block, "type", "")
                if btype == "server_tool_use":
                    if getattr(block, "name", "") == "web_search":
                        live_searches += 1
                        # OUR side of the cap: abort the moment the stream
                        # attempts a search past what the operator approved,
                        # no matter what the server allows.
                        if search_cap is not None and live_searches > search_cap:
                            raise ResearchBudgetExceeded(
                                f"aborted at search {live_searches} — the "
                                f"approved cap is {search_cap}")
                        _ceiling_check()
                        await on_progress("search", live_searches)
                    else:
                        await on_progress("filter", live_searches)
                elif btype == "text" and live_searches:
                    await on_progress("writing", live_searches)
            return await stream.get_final_message()

    async def _request():
        """One API round trip — plain create, or streamed when progress is on."""
        if on_progress is None:
            return await _bounded(client.messages.create(**kwargs))
        return await _bounded(_streamed())

    try:
        response = await _request()
        blocks = list(response.content)
        _absorb(response)
        while response.stop_reason == "pause_turn" and restarts < _MAX_PAUSE_RESTARTS:
            # Refuse to START another paid segment past the ceiling — the only
            # spend bound available to non-streamed calls, which have no
            # mid-flight events to guard on.
            _ceiling_check()
            restarts += 1
            if search_cap is not None:
                # A resumed request gets its own server-side max_uses budget, so
                # shrink it to what's left of the run's cap. The API floor is 1;
                # the mid-stream guard above covers that last gap.
                remaining_budget = max(search_cap - searches, 1)
                kwargs["tools"] = [dict(WEB_SEARCH_TOOL, max_uses=remaining_budget)]
            # Resume with the FULL accumulated assistant content. Rebuilding with
            # only the latest segment dropped earlier continuations' blocks from
            # the conversation on the second and later restarts.
            kwargs["messages"] = [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": blocks},
            ]
            response = await _request()
            blocks = blocks + list(response.content)
            _absorb(response)
    except Exception as exc:  # noqa: BLE001 — forensics first, then re-raise
        # A dead run still billed for everything that ran before the death.
        # The completion log below never fires on this path, so the money
        # would otherwise vanish from OUR ledger (it never vanishes from
        # Anthropic's) — record the partials here and hand them to the caller
        # on the exception itself.
        elapsed = time.monotonic() - started
        p_searches = max(live_searches, searches)
        p_in, p_out = tokens_in + seg_in, tokens_out + seg_out
        p_cost = estimate_cost_usd(effective_model, p_in, p_out,
                                   searches=p_searches)
        log.warning(
            "anthropic.run_failed error=%s searches_attempted=%d tokens_in~=%d "
            "tokens_out~=%d cost~=$%.2f elapsed=%.1fs",
            type(exc).__name__, p_searches, p_in, p_out, p_cost, elapsed,
        )
        try:
            exc.ridian_partial = {
                "searches": p_searches, "tokens_in": p_in, "tokens_out": p_out,
                "cost_usd": round(p_cost, 4),
            }
        except Exception:  # noqa: BLE001 — exotic exception types only
            pass
        raise

    elapsed = time.monotonic() - started
    if use_web_search:
        # Run forensics: billed searches (the money number), total tool rounds
        # (incl. free dynamic-filtering code execution), tokens for estimate
        # calibration. Verifiable against the Console usage dashboard.
        log.info(
            "anthropic.web_search searches_billed=%d tool_rounds=%d restarts=%d "
            "tokens_in=%d tokens_out=%d elapsed=%.1fs",
            searches, rounds, restarts, tokens_in, tokens_out, elapsed,
        )

    if response.stop_reason == "refusal":
        log.warning("anthropic.refusal stop_details=%s", getattr(response, "stop_details", None))
        text = ""
    else:
        text = _final_text(blocks)
    if return_stats:
        return TextAgentResult(
            text=text, searches=searches, restarts=restarts, tool_rounds=rounds,
            elapsed_seconds=elapsed, tokens_in=tokens_in, tokens_out=tokens_out,
        )
    return text
