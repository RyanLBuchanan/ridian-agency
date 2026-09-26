"""Agent definitions for Ridian Agency.

An "agent" is a named system prompt. Execution goes through Ridian's OpenAI
runtime for both the operator planner and one-shot specialists.
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
    return os.getenv("OPENAI_MODEL", "gpt-5.6-sol")


def research_model() -> str:
    return os.getenv("OPENAI_RESEARCH_MODEL", "") or default_model()


def script_model() -> str:
    return default_model()


# Curated per-run override targets for the sub-agent selectors (Research and
# Script) — the composer dropdowns may only pick from this list; anything
# else sent by a client is dropped at intake, never trusted. The PLANNER is
# deliberately absent from per-run selection: it enforces the gates and is
# changeable only from Settings.
ALLOWED_RESEARCH_MODELS: tuple[str, ...] = (
    "gpt-5.6-sol",
)

# Per-run effort levels for SUB-AGENT calls (output_config.effort — GA API
# param; the levels are taken verbatim by the API, there are no token
# budgets behind them on current models). The planner's effort is not
# per-run switchable, same protection as its model.
ALLOWED_EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high")


def model_supports_effort(model_id: str) -> bool:
    return True


@dataclass(frozen=True)
class PromptAgent:
    """A named system prompt executed through the OpenAI runtime."""

    name: str
    instructions: str
