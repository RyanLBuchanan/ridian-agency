"""Provider-neutral model dispatch for Ridian's one-shot agents."""

from __future__ import annotations

from . import anthropic_runtime, openai_runtime
from .settings_service import apply_to_environment, get_effective_value


async def run_text_agent(*args, **kwargs):
    """Prefer OpenAI for new work; retain Anthropic as a configured fallback."""
    apply_to_environment()
    if get_effective_value("OPENAI_API_KEY"):
        model = kwargs.get("model")
        if model and str(model).startswith("claude-"):
            kwargs.pop("model", None)
        return await openai_runtime.run_text_agent(*args, **kwargs)
    return await anthropic_runtime.run_text_agent(*args, **kwargs)
