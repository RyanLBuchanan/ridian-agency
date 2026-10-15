"""OpenAI planner compatibility layer for the existing Anthropic-decorated registry.

This module does not select a provider or alter approvals. It translates tool
schemas and executes the same wrapped functions (including approval staging).
The caller owns lifecycle, spend ceilings, and checkpoint persistence.
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI


def openai_tool_specs(tools: list[Any]) -> list[dict]:
    """Use the SDK-generated registry schemas; never infer permissive schemas."""
    specs = []
    for tool in tools:
        schema = tool.to_dict()
        specs.append({
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": schema["input_schema"],
            },
        })
    return specs


async def execute_registered_call(tool_by_name: dict, name: str, arguments: str) -> str:
    """Invoke the original approval-aware tool wrapper; reject unknown tools."""
    tool = tool_by_name.get(name)
    if tool is None:
        return json.dumps({"error": "unknown_tool", "tool": name})
    try:
        kwargs = json.loads(arguments)
        if not isinstance(kwargs, dict):
            raise ValueError("Tool arguments must be a JSON object")
    except (ValueError, TypeError) as exc:
        return json.dumps({"error": "invalid_arguments", "detail": str(exc)})
    # Anthropic beta tool wrappers are callable and return JSON text.
    result = await tool(**kwargs)
    return result if isinstance(result, str) else json.dumps(result, default=str)


async def run_openai_planner(
    *, system: str, messages: list[dict], tools: list[Any],
    model: str, max_turns: int = 24, on_turn=None, on_tool=None, on_step=None,
) -> tuple[str, list[dict]]:
    """Run bounded OpenAI Chat Completions tool loop, returning history.

    on_turn(response) is called after every provider response so the caller
    can enforce cost limits; on_tool(name, args) is called before execution
    for cancellation/approval policy. Exceptions propagate (fail closed).
    """
    client = AsyncOpenAI()
    history = [{"role": "system", "content": system}, *messages]
    specs = openai_tool_specs(tools)
    registry = {tool.name: tool for tool in tools}
    for _ in range(max_turns):
        response = await client.chat.completions.create(
            model=model, messages=history, tools=specs, tool_choice="auto", parallel_tool_calls=False,
        )
        if on_turn is not None:
            await on_turn(response)
        choice = response.choices[0]
        message = choice.message
        history.append(message.model_dump(exclude_none=True))
        calls = message.tool_calls or []
        if not calls:
            if on_step is not None:
                await on_step(history[1:])
            return (message.content or ""), history[1:]
        for call in calls:
            if on_tool is not None:
                await on_tool(call.function.name, call.function.arguments)
            output = await execute_registered_call(
                registry, call.function.name, call.function.arguments,
            )
            history.append({
                "role": "tool", "tool_call_id": call.id, "content": output,
            })
            if on_step is not None:
                await on_step(history[1:])
    raise RuntimeError("OpenAI planner reached maximum turns without completion")
