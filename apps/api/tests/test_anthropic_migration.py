"""Anthropic migration wiring — the pieces the SDK swap introduced.

Verifies (offline):
  - the @planner_tool wrapper preserves name/description/schema and
    JSON-encodes dict results for the tool runner;
  - the contextvar plumbing: the REAL registered tool objects (the ones the
    runner executes) find the bound OperatorContext and the safety gates fire
    through them end to end;
  - the planner system prompt renders with the live tool list;
  - run_text_agent resumes pause_turn turns and caps restarts.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.planner_agent import build_planner_system
from app.services import anthropic_runtime
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    for key in ("steps", "tools_used", "artifacts", "errors"):
        record.setdefault(key, [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


# --------------------------------------------------------------------------
# planner_tool wrapper: schema + JSON encoding
# --------------------------------------------------------------------------

def test_planner_tools_have_schemas_and_descriptions():
    for tool in t.PLANNER_TOOLS:
        d = tool.to_dict()
        assert d.get("description"), f"{tool.name} lost its description"
        assert d["input_schema"]["type"] == "object", tool.name
    # spot-check a required param survived the wrapper
    d = _tool("draft_gmail").to_dict()
    assert set(d["input_schema"]["required"]) == {"to", "subject", "body"}


def test_registered_tool_returns_json_string(tmp_path):
    """The real registered write_file tool: contextvar + gate + JSON wrapper,
    exactly the path the tool runner executes."""
    op = _ctx(tmp_path, {"deliverable_intent": False})   # conversational run
    set_current_operator(op)
    result = asyncio.run(_tool("write_file").call({
        "filename": "document.md", "content": "hello",
    }))
    assert isinstance(result, str)
    payload = json.loads(result)
    assert payload["reason"] == "no_deliverable_request"  # gate fired through the wrapper


def test_recipient_gate_fires_through_registered_tool(tmp_path, monkeypatch):
    """draft_gmail via the runner-executed object refuses a fabricated address."""
    monkeypatch.setattr(t.memory_service, "list_contacts", lambda: [])
    op = _ctx(tmp_path, {"deliverable_intent": True})
    set_current_operator(op)
    result = asyncio.run(_tool("draft_gmail").call({
        "to": "sarah@chamber.com", "subject": "Hi", "body": "Body",
    }))
    payload = json.loads(result)
    assert payload["reason"] == "recipient_unverified"
    assert op.record.get("awaiting_input") is True


def test_tools_require_bound_operator(tmp_path):
    """Without a bound OperatorContext the tool fails loudly, never silently."""
    from app.services.operator_context import _CURRENT_OPERATOR
    token = _CURRENT_OPERATOR.set(None)
    try:
        with pytest.raises(RuntimeError):
            asyncio.run(_tool("request_missing_info").call({"question": "x?"}))
    finally:
        _CURRENT_OPERATOR.reset(token)


# --------------------------------------------------------------------------
# planner system prompt
# --------------------------------------------------------------------------

def test_planner_system_renders_tool_registry():
    system = build_planner_system()
    assert "{TOOLS}" not in system
    for name in ("draft_gmail", "read_url", "create_slide_deck"):
        assert name in system


# --------------------------------------------------------------------------
# run_text_agent: pause_turn resume + text extraction
# --------------------------------------------------------------------------

class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


def _resp(stop_reason, text=""):
    blocks = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(stop_reason=stop_reason, content=blocks)


def test_run_text_agent_resumes_pause_turn(monkeypatch):
    fake = _FakeMessages([
        _resp("pause_turn", "partial"),
        _resp("end_turn", "final answer"),
    ])
    monkeypatch.setattr(anthropic_runtime, "get_client",
                        lambda: SimpleNamespace(messages=fake))
    out = asyncio.run(anthropic_runtime.run_text_agent("sys", "hi", use_web_search=True))
    assert out == "final answer"
    assert fake.calls == 2


def test_run_text_agent_caps_pause_restarts(monkeypatch):
    fake = _FakeMessages([_resp("pause_turn", "p")] * 10)
    monkeypatch.setattr(anthropic_runtime, "get_client",
                        lambda: SimpleNamespace(messages=fake))
    out = asyncio.run(anthropic_runtime.run_text_agent("sys", "hi"))
    assert fake.calls == 1 + anthropic_runtime._MAX_PAUSE_RESTARTS
    assert out == "p"   # last partial text, not an infinite loop
