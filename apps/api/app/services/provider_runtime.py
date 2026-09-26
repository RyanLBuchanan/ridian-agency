"""OpenAI dispatch for Ridian's one-shot agents."""

from __future__ import annotations

from . import openai_runtime
from .settings_service import apply_to_environment


async def run_text_agent(*args, **kwargs):
    """Run a Ridian specialist through OpenAI."""
    apply_to_environment()
    return await openai_runtime.run_text_agent(*args, **kwargs)
