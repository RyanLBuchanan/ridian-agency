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

import logging
from dataclasses import dataclass
from datetime import datetime

from anthropic import AsyncAnthropic

from ..agents import default_model

log = logging.getLogger("ridian.anthropic")

# max_uses bounds per-search billing on a single sub-agent turn: the research
# prompts ask for 5-12 sources, which a handful of searches covers.
WEB_SEARCH_TOOL: dict = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}

_MAX_PAUSE_RESTARTS = 4

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
    """Final text plus run forensics, for callers that must verify grounding."""
    text: str
    searches: int
    restarts: int


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
):
    """One-shot agent: system prompt + user input → final text.

    ``user_input`` is a plain string or an Anthropic content-block list
    (for multimodal input). With ``use_web_search`` the server-side web search
    tool is attached and ``pause_turn`` continuations are handled. With
    ``return_stats`` returns a :class:`TextAgentResult` (text + search count)
    instead of a plain string, so research callers can flag zero-search runs
    as ungrounded. ``model`` overrides default_model() for this call (the
    research sub-agents pass research_model()).
    """
    client = get_client()
    kwargs: dict = {
        "model": model or default_model(),
        "max_tokens": max_tokens,
        "system": f"{date_line()}\n\n{system}",
        "messages": [{"role": "user", "content": user_input}],
    }
    if use_web_search:
        kwargs["tools"] = [WEB_SEARCH_TOOL]

    def _search_count(resp) -> int:
        return sum(1 for b in resp.content if getattr(b, "type", "") == "server_tool_use")

    response = await client.messages.create(**kwargs)
    blocks = list(response.content)
    searches = _search_count(response)
    restarts = 0
    while response.stop_reason == "pause_turn" and restarts < _MAX_PAUSE_RESTARTS:
        restarts += 1
        # Resume with the FULL accumulated assistant content. Rebuilding with
        # only the latest segment dropped earlier continuations' blocks from
        # the conversation on the second and later restarts.
        kwargs["messages"] = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": blocks},
        ]
        response = await client.messages.create(**kwargs)
        blocks = blocks + list(response.content)
        searches += _search_count(response)

    if use_web_search:
        # Billing sanity: web search is billed per search. Surfaced in the
        # backend log so a live run's search count is verifiable.
        log.info("anthropic.web_search searches=%d restarts=%d", searches, restarts)

    if response.stop_reason == "refusal":
        log.warning("anthropic.refusal stop_details=%s", getattr(response, "stop_details", None))
        text = ""
    else:
        text = _final_text(blocks)
    if return_stats:
        return TextAgentResult(text=text, searches=searches, restarts=restarts)
    return text
