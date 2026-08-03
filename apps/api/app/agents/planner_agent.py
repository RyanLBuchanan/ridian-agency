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
from ..services.settings_service import load_settings


def _operator_profile() -> str:
    """OPERATOR PROFILE block from Settings — the prompt file carries no
    hardcoded identity (v4.8: operator_name/operator_email/company_name were
    saved by the UI but read by nothing while the prompt said 'Ryan
    Buchanan' forever). Unset fields degrade honestly instead of guessing."""
    s = load_settings()
    name = (s.get("operator_name") or "").strip()
    email = (s.get("operator_email") or "").strip()
    company = (s.get("company_name") or "").strip()
    lines = ["OPERATOR PROFILE (live from Settings):"]
    lines.append(f"- Operator: {name}" if name else
                 "- Operator: (name not set — stay generic; never invent one)")
    if email:
        lines.append(f"- Email: {email}")
    if company:
        lines.append(f"- Company: {company}")
    return "\n".join(lines)


def build_planner_system() -> str:
    """Render the planner system prompt with the live tool list and the
    operator profile spliced in.

    Per the memo's risk-mitigation note: "Planner prompt explicitly lists
    the tool registry and forbids inventing tools. Add a capability
    discovery step early in the planner prompt where it grounds its plan
    in actual tool names." Built per-operation so model/prompt/settings
    changes take effect on the next run.
    """
    raw = (PROMPTS_DIR / "planner_prompt.txt").read_text(encoding="utf-8")
    return (raw
            .replace("{TOOLS}", tool_capability_summary())
            .replace("{OPERATOR_PROFILE}", _operator_profile()))
