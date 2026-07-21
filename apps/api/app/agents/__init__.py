"""Agent definitions for Ridian Agency (Anthropic-powered).

An "agent" here is just a named system prompt — execution goes through
``services.anthropic_runtime.run_text_agent`` (one-shot specialists) or the
Tool Runner loop in ``services.operator_service`` (the operator planner).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from ..services.runtime_paths import resource_base  # noqa: E402

# v4.2: routed through resource_base() so the frozen build reads prompts
# from the PyInstaller bundle; dev resolves to app/prompts exactly as before.
PROMPTS_DIR = resource_base() / "app" / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def default_model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")


def research_model() -> str:
    """Model for the research/packet sub-agents ONLY. They spend their time on
    web-search round-trips + summarizing, where Sonnet-tier quality holds up
    and the foreground wait matters — the planner (tool selection, gates
    context, receipts) stays on default_model()."""
    return os.getenv("ANTHROPIC_RESEARCH_MODEL", "claude-sonnet-5")


def script_model() -> str:
    """Model for the audiobook script-writer sub-agent. Falls back to
    default_model() — the script writer historically rode the planner model,
    and picking nothing preserves that behavior exactly."""
    return os.getenv("ANTHROPIC_SCRIPT_MODEL", "") or default_model()


# Curated per-run override targets for the sub-agent selectors (Research and
# Script) — the composer dropdowns may only pick from this list; anything
# else sent by a client is dropped at intake, never trusted. The PLANNER is
# deliberately absent from per-run selection: it enforces the gates and is
# changeable only from Settings.
ALLOWED_RESEARCH_MODELS: tuple[str, ...] = (
    "claude-sonnet-5",
    "claude-opus-4-8",
    "claude-haiku-4-5",
    "claude-fable-5",
)

# Per-run effort levels for SUB-AGENT calls (output_config.effort — GA API
# param; the levels are taken verbatim by the API, there are no token
# budgets behind them on current models). The planner's effort is not
# per-run switchable, same protection as its model.
ALLOWED_EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high")


def model_supports_effort(model_id: str) -> bool:
    """Haiku 4.5 rejects output_config.effort — omit it there rather than 400."""
    return not (model_id or "").startswith("claude-haiku")


@dataclass(frozen=True)
class PromptAgent:
    """A named system prompt. Replaces the OpenAI Agents SDK ``Agent`` object
    for one-shot specialists — run it with anthropic_runtime.run_text_agent."""

    name: str
    instructions: str
