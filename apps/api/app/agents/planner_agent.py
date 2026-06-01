"""The Ridian Operator planner agent.

Single general-purpose agent that owns the full Operator tool registry.
Receives the operator's natural-language command as input + an
``OperatorContext`` as run context. Picks tools, chains them, verifies
each step, and emits a short final summary.

No keyword intent recognizer upstream — this agent IS the routing layer.
"""

from __future__ import annotations

from agents import Agent

from . import default_model
from ..services.operator_tools import PLANNER_TOOLS, tool_capability_summary


def _load_prompt_with_tools() -> str:
    """Render the planner prompt with the live tool list spliced in.

    Per the memo's risk-mitigation note: "Planner prompt explicitly lists
    the tool registry and forbids inventing tools. Add a capability
    discovery step early in the planner prompt where it grounds its plan
    in actual tool names."
    """
    from . import PROMPTS_DIR
    raw = (PROMPTS_DIR / "planner_prompt.txt").read_text(encoding="utf-8")
    return raw.replace("{TOOLS}", tool_capability_summary())


def build_planner_agent() -> Agent:
    """Construct a fresh planner agent. Built per-operation so model swaps
    via the Settings panel take effect on the next run."""
    return Agent(
        name="Ridian Operator",
        instructions=_load_prompt_with_tools(),
        model=default_model(),
        tools=PLANNER_TOOLS,
    )
