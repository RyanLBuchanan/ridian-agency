"""The Ridian Operator planner — system prompt for the Anthropic tool runner.

Single general-purpose agent that owns the full Operator tool registry.
Receives the operator's natural-language command as input; the active run's
``OperatorContext`` rides on a task-local contextvar (see operator_context).
Picks tools, chains them, verifies each step, and emits a short final summary.

No keyword intent recognizer upstream — this agent IS the routing layer.
"""

from __future__ import annotations

from . import PROMPTS_DIR
from ..services.operator_tools import tool_capability_summary


def build_planner_system() -> str:
    """Render the planner system prompt with the live tool list spliced in.

    Per the memo's risk-mitigation note: "Planner prompt explicitly lists
    the tool registry and forbids inventing tools. Add a capability
    discovery step early in the planner prompt where it grounds its plan
    in actual tool names." Built per-operation so model/prompt changes take
    effect on the next run.
    """
    raw = (PROMPTS_DIR / "planner_prompt.txt").read_text(encoding="utf-8")
    return raw.replace("{TOOLS}", tool_capability_summary())
