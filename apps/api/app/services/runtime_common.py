"""Shared runtime contracts for Ridian's OpenAI intelligence layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Keep the existing research approval language stable. OpenAI web-search cost
# accounting is token-based in openai_runtime; this constant is only used for
# conservative preflight messaging and is intentionally zero until Ridian has
# a provider-reported per-search line item to ingest.
SEARCH_COST_USD = 0.0
WEB_SEARCH_TOOL: dict = {"type": "web_search", "name": "web_search", "max_uses": 8}


class RunDeadlineExceeded(RuntimeError):
    """A model turn exceeded Ridian's wall-clock ceiling."""


class ResearchBudgetExceeded(RuntimeError):
    """A research turn exceeded its approved search budget."""


class RunBudgetExceeded(RuntimeError):
    """A run crossed the operator's hard dollar ceiling."""


def date_line() -> str:
    """Live date grounding injected into every model context."""
    now = datetime.now()
    return f"Today's date: {now.strftime('%A, %B %d, %Y')} ({now.date().isoformat()})."


@dataclass
class TextAgentResult:
    """Final text plus run forensics used by deterministic grounding gates."""

    text: str
    searches: int
    restarts: int
    tool_rounds: int = 0
    elapsed_seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    source_urls: tuple = ()
    queries: tuple = ()
