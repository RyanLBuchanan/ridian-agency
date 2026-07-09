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

from anthropic import AsyncAnthropic

from ..agents import default_model

log = logging.getLogger("ridian.anthropic")

WEB_SEARCH_TOOL: dict = {"type": "web_search_20260209", "name": "web_search"}

_MAX_PAUSE_RESTARTS = 4

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
        _client = AsyncAnthropic()
        _client_key = key
    return _client


def _text_of(response) -> str:
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


async def run_text_agent(
    system: str,
    user_input,
    *,
    use_web_search: bool = False,
    max_tokens: int = 16000,
) -> str:
    """One-shot agent: system prompt + user input → final text.

    ``user_input`` is a plain string or an Anthropic content-block list
    (for multimodal input). With ``use_web_search`` the server-side web search
    tool is attached and ``pause_turn`` continuations are handled.
    """
    client = get_client()
    kwargs: dict = {
        "model": default_model(),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_input}],
    }
    if use_web_search:
        kwargs["tools"] = [WEB_SEARCH_TOOL]

    response = await client.messages.create(**kwargs)
    restarts = 0
    while response.stop_reason == "pause_turn" and restarts < _MAX_PAUSE_RESTARTS:
        restarts += 1
        kwargs["messages"] = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": response.content},
        ]
        response = await client.messages.create(**kwargs)

    if response.stop_reason == "refusal":
        log.warning("anthropic.refusal stop_details=%s", getattr(response, "stop_details", None))
        return ""
    return _text_of(response)
