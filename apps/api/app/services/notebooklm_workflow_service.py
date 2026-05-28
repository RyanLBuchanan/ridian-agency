"""NotebookLM Prompt + Audio Overview Builder workflow.

A single OpenAI Agents SDK agent produces a ready-to-paste Markdown
package for Google's NotebookLM. The package is saved to its own per-run
folder under ``outputs/<timestamp>_<slug>/notebooklm_package.md``.

Kept deliberately simple: one agent, one ``Runner.run``, one artifact.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agents import Agent, Runner, trace

from ..agents import default_model, load_prompt
from .artifact_service import create_run_folder, write_artifact
from .settings_service import apply_to_environment

log = logging.getLogger("ridian.notebooklm")

ALLOWED_PURPOSES = (
    "Learn",
    "Strategy",
    "Content creation",
    "Business planning",
    "Teaching / training",
)

ALLOWED_AUDIENCES = (
    "Ryan",
    "Entrepreneurs",
    "Educators",
    "Clients",
    "General audience",
)

ALLOWED_OUTPUT_TYPES = (
    "NotebookLM source prompt",
    "Audio Overview prompt",
    "Full NotebookLM package",
)


@dataclass
class NotebookLMInput:
    subject: str
    purpose: str = "Learn"
    audience: str = "Ryan"
    output_type: str = "Full NotebookLM package"
    notes: str = ""


@dataclass
class NotebookLMResult:
    artifact_folder: Path
    notebooklm_package: str


def _build_agent() -> Agent:
    return Agent(
        name="NotebookLM Package Builder",
        instructions=load_prompt("notebooklm_prompt.txt"),
        model=default_model(),
    )


def _format_input(payload: NotebookLMInput) -> str:
    purpose = payload.purpose or "Learn"
    audience = payload.audience or "Ryan"
    output_type = payload.output_type or "Full NotebookLM package"

    parts = [
        f"Subject: {payload.subject.strip()}",
        f"Purpose: {purpose}",
        f"Audience: {audience}",
        f"Output type: {output_type}",
    ]
    if (payload.notes or "").strip():
        parts.append(f"\nOperator notes:\n{payload.notes.strip()}")

    if output_type == "NotebookLM source prompt":
        parts.append(
            "\nScope guidance: produce ALL sections of the package, but write "
            "the Source Gathering Prompt section in extra depth — it is the "
            "primary deliverable for this run."
        )
    elif output_type == "Audio Overview prompt":
        parts.append(
            "\nScope guidance: produce ALL sections of the package, but write "
            "the NotebookLM Audio Overview Prompt section in extra depth — it "
            "is the primary deliverable for this run."
        )
    else:
        parts.append(
            "\nScope guidance: produce the full package with every section "
            "polished and copy-paste ready."
        )

    return "\n".join(parts)


def _slug_for_run(payload: NotebookLMInput) -> str:
    seed = payload.subject or "notebooklm"
    return f"notebooklm - {seed[:50]}"


async def run_notebooklm_workflow(payload: NotebookLMInput) -> NotebookLMResult:
    apply_to_environment()

    agent = _build_agent()
    agent.model = default_model()

    folder = create_run_folder(_slug_for_run(payload))
    formatted_input = _format_input(payload)

    with trace("ridian-agency.notebooklm"):
        result = await Runner.run(agent, input=formatted_input)

    package = (result.final_output or "").strip()
    if not package:
        package = (
            "# NotebookLM Package\n\n"
            "_The model returned no output. Try a different subject or output type._"
        )

    write_artifact(folder, "notebooklm_package.md", package)
    write_artifact(folder, "task.txt", formatted_input)

    log.info("notebooklm.complete folder=%s len=%d", folder, len(package))

    return NotebookLMResult(
        artifact_folder=folder,
        notebooklm_package=package,
    )
