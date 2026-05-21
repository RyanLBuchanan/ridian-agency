"""Agent definitions for Ridian Agency."""

import os
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def default_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
