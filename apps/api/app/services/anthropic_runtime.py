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

# Wall-clock ceiling for ONE text-agent turn (all pause segments combined).
# The 300s request timeout is a per-read idle timeout — on a STREAMING turn
# bytes flow continuously and it never fires (the 2026-07-15 live run streamed
# for 9m04s untouched). This cap is independent of byte flow. The observed
# research band is 4-9 minutes; 12 gives headroom without ever allowing an
# unbounded run.
_MAX_TURN_SECONDS = 720.0


class RunDeadlineExceeded(RuntimeError):
    """A text-agent turn exceeded the wall-clock ceiling."""


class ResearchBudgetExceeded(RuntimeError):
    """A streamed turn attempted more billed searches than the approved cap."""

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

    live_searches = 0   # stream-observed billed proxy, across segments

    async def _streamed():
        nonlocal live_searches
        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if getattr(event, "type", "") != "content_block_start":
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

    response = await _request()
    blocks = list(response.content)
    billed = _billed_searches(response)
    searches = billed if billed is not None else _search_count(response)
    rounds = _round_count(response)
    tokens_in, tokens_out = _tokens(response)
    restarts = 0
    while response.stop_reason == "pause_turn" and restarts < _MAX_PAUSE_RESTARTS:
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
        seg_billed = _billed_searches(response)
        searches += seg_billed if seg_billed is not None else _search_count(response)
        rounds += _round_count(response)
        t_in, t_out = _tokens(response)
        tokens_in += t_in
        tokens_out += t_out

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
