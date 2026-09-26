"""OpenAI runtime for Ridian Operator.

Ridian uses OpenAI for planner reasoning, specialist agents, hosted web search,
voice features, and function-tool orchestration. Conversation state remains
local with store=False; Ridian executes tool calls behind its existing approval,
provenance, and audit gates.
"""

from __future__ import annotations

import json
import inspect
import logging
import os
import time
from typing import Any, get_args, get_origin

from openai import AsyncOpenAI

from .runtime_common import RunBudgetExceeded, TextAgentResult, date_line

log = logging.getLogger("ridian.openai")

_DEFAULT_MODEL = "gpt-5.6-sol"
_DEFAULT_RESEARCH_MODEL = "gpt-5.6-sol"

# Deliberately conservative until OpenAI exposes a stable machine-readable
# price source in this app. A high estimate protects Ridian's spend fence.
_MODEL_RATES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-5.6": (10.0, 50.0),
}
_TOP_TIER_RATES = (10.0, 50.0)

_client: AsyncOpenAI | None = None
_client_key: str | None = None


def default_model() -> str:
    return (os.getenv("OPENAI_MODEL") or "").strip() or _DEFAULT_MODEL


def research_model() -> str:
    return (os.getenv("OPENAI_RESEARCH_MODEL") or "").strip() or _DEFAULT_RESEARCH_MODEL


def get_client() -> AsyncOpenAI:
    global _client, _client_key
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if _client is None or _client_key != key:
        _client = AsyncOpenAI(api_key=key, timeout=300.0, max_retries=1)
        _client_key = key
    return _client


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int, *, searches: int = 0) -> float:
    rate_in, rate_out = next(
        (rates for prefix, rates in _MODEL_RATES_PER_MTOK.items()
         if (model or "").startswith(prefix)),
        _TOP_TIER_RATES,
    )
    # Web-search pricing is deliberately not guessed here. The provider's
    # token usage is always counted; hosted-tool line items stay visible in
    # the OpenAI dashboard until Ridian has a stable usage field to ingest.
    return tokens_in / 1e6 * rate_in + tokens_out / 1e6 * rate_out


def _dump(item: Any) -> Any:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", exclude_none=True, by_alias=True)
    return item


def _json_type(annotation: Any) -> dict:
    """Small JSON-schema mapper for Ridian's typed planner-tool signatures."""
    if annotation is inspect._empty or annotation is Any:
        return {}
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list:
        return {"type": "array", "items": _json_type(args[0]) if args else {}}
    if origin is dict:
        return {"type": "object"}
    if origin is not None and type(None) in args:
        non_none = [a for a in args if a is not type(None)]
        return _json_type(non_none[0]) if len(non_none) == 1 else {}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    return {}


class FunctionTool:
    """Provider-independent wrapper around an async Ridian tool function."""

    def __init__(self, fn):
        self.fn = fn
        self.name = fn.__name__
        self.description = inspect.getdoc(fn) or ""
        sig = inspect.signature(fn)
        properties = {}
        required = []
        for name, param in sig.parameters.items():
            properties[name] = _json_type(param.annotation)
            if param.default is inspect._empty:
                required.append(name)
        self.input_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}

    async def call(self, args: dict):
        return await self.fn(**args)


def tool_from_callable(fn):
    """Register a Ridian async function for OpenAI function calling."""
    return FunctionTool(fn)


def _tool_specs(tools: list) -> list[dict]:
    specs: list[dict] = []
    for tool in tools:
        d = tool.to_dict()
        specs.append({
            "type": "function",
            "name": tool.name,
            "description": d.get("description") or "",
            "parameters": d.get("input_schema") or {"type": "object", "properties": {}},
        })
    return specs


def _usage(response) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
    )


def _search_forensics(response) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    searches = 0
    urls: list[str] = []
    queries: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", "") == "web_search_call":
            searches += 1
            action = getattr(item, "action", None)
            q = getattr(action, "query", None) if action is not None else None
            if q:
                queries.append(str(q))
            for qq in (getattr(action, "queries", None) or []) if action is not None else []:
                if qq:
                    queries.append(str(qq))
        if getattr(item, "type", "") != "message":
            continue
        for part in getattr(item, "content", []) or []:
            for ann in getattr(part, "annotations", []) or []:
                u = getattr(ann, "url", None)
                if u and str(u) not in urls:
                    urls.append(str(u))
    return searches, tuple(urls), tuple(queries)


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
    """One-shot Ridian sub-agent on the Responses API."""
    client = get_client()
    effective_model = model or (research_model() if use_web_search else default_model())
    kwargs: dict[str, Any] = {
        "model": effective_model,
        "instructions": f"{date_line()}\n\n{system}",
        "input": user_input,
        "store": False,
    }
    if effort:
        kwargs["reasoning"] = {"effort": "high" if effort == "high" else "medium"}
    if use_web_search:
        kwargs["tools"] = [{"type": "web_search"}]

    started = time.monotonic()
    response = await client.responses.create(**kwargs)
    tokens_in, tokens_out = _usage(response)
    searches, source_urls, queries = _search_forensics(response)
    cost = estimate_cost_usd(effective_model, tokens_in, tokens_out, searches=searches)
    if cost_ceiling is not None and spent_usd + cost > cost_ceiling:
        raise RunBudgetExceeded(
            f"stopped at approximately ${spent_usd + cost:.2f} of the "
            f"${cost_ceiling:.2f} run cost ceiling"
        )
    if on_progress is not None:
        for n in range(1, searches + 1):
            await on_progress("search", n)
        if searches:
            await on_progress("writing", searches)

    text = (getattr(response, "output_text", "") or "").strip()
    if not return_stats:
        return text
    return TextAgentResult(
        text=text,
        searches=searches,
        restarts=0,
        elapsed_seconds=time.monotonic() - started,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        source_urls=source_urls,
        queries=queries,
    )


async def run_planner_turn(*, system: str, input_items: list, tools: list,
                           max_turns: int, effort: str = "medium") -> dict:
    """Run the OpenAI planner/tool loop until it answers or hits max_turns.

    Returns the JSON-safe conversation continuation, response text and total
    token usage. Ridian executes every function locally through the existing
    registered tool objects, preserving all approval and provenance gates.
    """
    client = get_client()
    model = default_model()
    by_name = {tool.name: tool for tool in tools}
    items = list(input_items or [])
    total_in = total_out = 0
    tool_names: list[str] = []
    last_text = ""

    for _ in range(max_turns):
        response = await client.responses.create(
            model=model,
            instructions=system,
            input=items,
            tools=_tool_specs(tools),
            reasoning={"effort": effort},
            store=False,
        )
        t_in, t_out = _usage(response)
        total_in += t_in
        total_out += t_out
        output = [_dump(x) for x in (getattr(response, "output", []) or [])]
        # Responses input accepts assistant messages/function calls, but
        # reasoning-only output items are opaque provider state. With store=False
        # we keep only replayable conversation items in Ridian's local session.
        replayable = [x for x in output
                      if isinstance(x, dict) and x.get("type") in ("message", "function_call")]
        items.extend(replayable)
        text = (getattr(response, "output_text", "") or "").strip()
        if text:
            last_text = text

        calls = [x for x in (getattr(response, "output", []) or [])
                 if getattr(x, "type", "") == "function_call"]
        if not calls:
            return {
                "items": items,
                "text": last_text,
                "tokens_in": total_in,
                "tokens_out": total_out,
                "tool_names": tool_names,
                "completed": True,
                "model": model,
            }

        for call in calls:
            name = str(getattr(call, "name", "") or "")
            call_id = str(getattr(call, "call_id", "") or "")
            tool_names.append(name)
            tool = by_name.get(name)
            if tool is None:
                result = json.dumps({"error": f"Unknown tool: {name}"})
            else:
                raw = getattr(call, "arguments", "") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                except (ValueError, TypeError):
                    args = {}
                result = await tool.call(args)
                if not isinstance(result, str):
                    result = json.dumps(result, default=str)
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": result,
            })

    return {
        "items": items,
        "text": last_text,
        "tokens_in": total_in,
        "tokens_out": total_out,
        "tool_names": tool_names,
        "completed": False,
        "model": model,
    }
