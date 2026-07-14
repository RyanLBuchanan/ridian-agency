"""Sequential pipeline that runs the full Ridian Agency workflow.

Pipeline:
  task -> research -> writer -> reviewer -> presentation -> email

Each step is a one-shot Claude call on a specialist system prompt via
``anthropic_runtime.run_text_agent``. Deterministic order, five artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..agents.email_agent import email_agent
from ..agents.presentation_agent import presentation_agent
from ..agents.research_agent import research_agent
from ..agents.reviewer_agent import reviewer_agent
from ..agents.writer_agent import writer_agent
from .anthropic_runtime import run_text_agent
from .artifact_service import create_run_folder, write_artifact
from .settings_service import apply_to_environment


@dataclass
class WorkflowResult:
    artifact_folder: Path
    research_summary: str
    business_document: str
    slide_outline: str
    draft_email: str


async def run_workflow(task: str) -> WorkflowResult:
    # Pick up any settings the operator changed since startup (API key/model
    # are read from env at request time by the Anthropic client).
    apply_to_environment()
    folder = create_run_folder(task)

    # 1. Research. The old research agent had a get_today tool; the date is
    #    injected directly now so research stays grounded in "now".
    research_summary = (await run_text_agent(
        research_agent.instructions,
        f"Today's date: {date.today().isoformat()}\n\nOperator task:\n{task}",
    )).strip()
    write_artifact(folder, "research_summary.md", research_summary)

    # 2. Writer
    writer_input = (
        f"Operator task:\n{task}\n\n"
        f"Market research summary:\n{research_summary}"
    )
    draft_document = (await run_text_agent(writer_agent.instructions, writer_input)).strip()

    # 3. Reviewer (polishes the draft)
    business_document = (await run_text_agent(reviewer_agent.instructions, draft_document)).strip()
    write_artifact(folder, "business_document.md", business_document)

    # 4. Presentation
    slide_outline = (await run_text_agent(presentation_agent.instructions, business_document)).strip()
    write_artifact(folder, "slide_outline.md", slide_outline)

    # 5. Email
    draft_email = (await run_text_agent(email_agent.instructions, business_document)).strip()
    write_artifact(folder, "draft_email.md", draft_email)

    # Also save the original task for traceability
    write_artifact(folder, "task.txt", task)

    return WorkflowResult(
        artifact_folder=folder,
        research_summary=research_summary,
        business_document=business_document,
        slide_outline=slide_outline,
        draft_email=draft_email,
    )
