"""Sequential pipeline that runs the full Ridian Agency workflow.

Pipeline:
  task -> research -> writer -> reviewer -> presentation -> email

Each step is a separate ``Runner.run`` call on a specialist agent. The whole
pipeline runs inside a single SDK ``trace`` so it shows up as one workflow in
the OpenAI tracing dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents import Runner, trace

from ..agents import default_model
from ..agents.email_agent import email_agent
from ..agents.presentation_agent import presentation_agent
from ..agents.research_agent import research_agent
from ..agents.reviewer_agent import reviewer_agent
from ..agents.writer_agent import writer_agent
from .artifact_service import create_run_folder, write_artifact
from .settings_service import apply_to_environment

_PIPELINE_AGENTS = (
    research_agent,
    writer_agent,
    reviewer_agent,
    presentation_agent,
    email_agent,
)


def _apply_runtime_settings() -> None:
    """Pick up any settings the operator changed since startup.

    The Agent objects were constructed at import time with whatever
    ``default_model()`` resolved to back then. Mirror the latest settings
    into ``os.environ`` and reassign each agent's ``model`` so a new
    model name saved through the Settings panel takes effect on the next
    workflow run without a backend restart. The OpenAI SDK reads
    ``OPENAI_API_KEY`` from env at request time, so that key flows
    through automatically once it's in ``os.environ``.
    """
    apply_to_environment()
    model = default_model()
    for agent in _PIPELINE_AGENTS:
        agent.model = model


@dataclass
class WorkflowResult:
    artifact_folder: Path
    research_summary: str
    business_document: str
    slide_outline: str
    draft_email: str


async def run_workflow(task: str) -> WorkflowResult:
    _apply_runtime_settings()
    folder = create_run_folder(task)

    with trace("ridian-agency.workflow"):
        # 1. Research
        research_result = await Runner.run(
            research_agent,
            input=f"Operator task:\n{task}",
        )
        research_summary = research_result.final_output.strip()
        write_artifact(folder, "research_summary.md", research_summary)

        # 2. Writer
        writer_input = (
            f"Operator task:\n{task}\n\n"
            f"Market research summary:\n{research_summary}"
        )
        writer_result = await Runner.run(writer_agent, input=writer_input)
        draft_document = writer_result.final_output.strip()

        # 3. Reviewer (polishes the draft)
        reviewer_result = await Runner.run(reviewer_agent, input=draft_document)
        business_document = reviewer_result.final_output.strip()
        write_artifact(folder, "business_document.md", business_document)

        # 4. Presentation
        presentation_result = await Runner.run(
            presentation_agent, input=business_document
        )
        slide_outline = presentation_result.final_output.strip()
        write_artifact(folder, "slide_outline.md", slide_outline)

        # 5. Email
        email_result = await Runner.run(email_agent, input=business_document)
        draft_email = email_result.final_output.strip()
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
