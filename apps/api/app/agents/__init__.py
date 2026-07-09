"""Agent definitions for Ridian Agency (Anthropic-powered).

An "agent" here is just a named system prompt — execution goes through
``services.anthropic_runtime.run_text_agent`` (one-shot specialists) or the
Tool Runner loop in ``services.operator_service`` (the operator planner).
"""

import os
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def default_model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")


@dataclass(frozen=True)
class PromptAgent:
    """A named system prompt. Replaces the OpenAI Agents SDK ``Agent`` object
    for one-shot specialists — run it with anthropic_runtime.run_text_agent."""

    name: str
    instructions: str
