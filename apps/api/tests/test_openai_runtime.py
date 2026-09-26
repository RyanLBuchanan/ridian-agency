"""OpenAI primary-runtime migration contract.

Offline tests: no provider calls. These pin the migration boundary so future
changes cannot silently hard-wire the Operator back to one vendor.
"""
from types import SimpleNamespace

from app.services import openai_runtime, provider_runtime


def test_openai_function_specs_reuse_existing_ridian_schema():
    tool = SimpleNamespace(
        name="demo",
        to_dict=lambda: {
            "description": "Do the thing.",
            "input_schema": {
                "type": "object",
                "properties": {"subject": {"type": "string"}},
                "required": ["subject"],
            },
        },
    )
    spec = openai_runtime._tool_specs([tool])[0]
    assert spec["type"] == "function"
    assert spec["name"] == "demo"
    assert spec["parameters"]["required"] == ["subject"]


def test_openai_model_defaults_are_explicit(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_RESEARCH_MODEL", raising=False)
    assert openai_runtime.default_model() == "gpt-5.6-sol"
    assert openai_runtime.research_model() == "gpt-5.6-sol"


def test_provider_runtime_prefers_openai(monkeypatch):
    monkeypatch.setattr(provider_runtime, "apply_to_environment", lambda: None)
    monkeypatch.setattr(
        provider_runtime, "get_effective_value",
        lambda name: "sk-test" if name == "OPENAI_API_KEY" else None,
    )
    seen = {}

    async def openai(*args, **kwargs):
        seen["provider"] = "openai"
        return "ok"

    async def anthropic(*args, **kwargs):
        seen["provider"] = "anthropic"
        return "wrong"

    monkeypatch.setattr(provider_runtime.openai_runtime, "run_text_agent", openai)
    monkeypatch.setattr(provider_runtime.anthropic_runtime, "run_text_agent", anthropic)

    import asyncio
    assert asyncio.run(provider_runtime.run_text_agent("sys", "hi")) == "ok"
    assert seen["provider"] == "openai"


def test_provider_runtime_falls_back_to_anthropic(monkeypatch):
    monkeypatch.setattr(provider_runtime, "apply_to_environment", lambda: None)
    monkeypatch.setattr(provider_runtime, "get_effective_value", lambda _name: None)
    seen = {}

    async def anthropic(*args, **kwargs):
        seen["provider"] = "anthropic"
        return "ok"

    monkeypatch.setattr(provider_runtime.anthropic_runtime, "run_text_agent", anthropic)

    import asyncio
    assert asyncio.run(provider_runtime.run_text_agent("sys", "hi")) == "ok"
    assert seen["provider"] == "anthropic"
