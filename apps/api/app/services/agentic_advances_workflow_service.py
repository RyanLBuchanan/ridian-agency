"""Agentic Advances Daily Brief workflow.

A single OpenAI Agents SDK agent equipped with the hosted ``WebSearchTool``
produces a Markdown brief of significant agentic AI developments relevant
to Ridian. The brief is saved to its own per-run folder under
``outputs/<timestamp>_<slug>/agentic_advances_brief.md``.

Kept deliberately simple: one agent, one ``Runner.run``, one artifact.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agents import Agent, Runner, WebSearchTool, trace

from ..agents import default_model, load_prompt
from .artifact_service import create_run_folder, write_artifact
from .settings_service import apply_to_environment

log = logging.getLogger("ridian.agentic_advances")

ALLOWED_TIME_WINDOWS = (
    "Last 24 hours",
    "Last 7 days",
    "Last 30 days",
    "Last 90 days",
)

ALLOWED_OUTPUT_DEPTHS = (
    "Quick brief",
    "Strategic brief",
    "Deep research brief",
)


@dataclass
class AgenticAdvancesInput:
    topic_focus: str = ""
    time_window: str = "Last 7 days"
    output_depth: str = "Strategic brief"


@dataclass
class AgenticAdvancesResult:
    artifact_folder: Path
    agentic_advances_brief: str


def _build_agent() -> Agent:
    return Agent(
        name="Agentic Advances Analyst",
        instructions=load_prompt("agentic_advances_prompt.txt"),
        model=default_model(),
        tools=[WebSearchTool(search_context_size="high")],
    )


def _format_input(payload: AgenticAdvancesInput) -> str:
    time_window = payload.time_window or "Last 7 days"
    output_depth = payload.output_depth or "Strategic brief"

    parts = [
        f"Time window: {time_window}",
        f"Output depth: {output_depth}",
    ]
    if (payload.topic_focus or "").strip():
        parts.append(f"\nTopic focus (in addition to standard Ridian focus areas):\n{payload.topic_focus.strip()}")
    else:
        parts.append(
            "\nNo topic focus provided. Use the default Ridian focus areas: "
            "agentic AI, OpenAI Agents SDK, agent memory, computer-use / browser "
            "agents, MCP, AI productivity, AI in education, workflow automation, "
            "and local-business consulting."
        )

    depth_guidance = {
        "Quick brief": (
            "\nDepth guidance: keep the brief tight — aim for 3-4 items in "
            "Significant Advances and 2-3 items in Watchlist. Prioritize "
            "ruthlessly."
        ),
        "Strategic brief": (
            "\nDepth guidance: medium depth — 4-6 items in Significant Advances, "
            "3-5 in Watchlist. Lean toward strategic implications over technical "
            "minutiae."
        ),
        "Deep research brief": (
            "\nDepth guidance: deep dive — 6-8 items in Significant Advances, "
            "5-7 in Watchlist, and expand the Ridian Opportunities section to "
            "5+ concrete near-term moves."
        ),
    }
    parts.append(depth_guidance.get(output_depth, depth_guidance["Strategic brief"]))

    return "\n".join(parts)


def _slug_for_run(payload: AgenticAdvancesInput) -> str:
    seed = payload.topic_focus or "agentic-advances"
    window = (payload.time_window or "").replace(" ", "-").lower()
    return f"agentic-advances - {window} - {seed[:40]}"


async def run_agentic_advances_workflow(payload: AgenticAdvancesInput) -> AgenticAdvancesResult:
    apply_to_environment()

    agent = _build_agent()
    agent.model = default_model()

    folder = create_run_folder(_slug_for_run(payload))
    formatted_input = _format_input(payload)

    with trace("ridian-agency.agentic-advances"):
        result = await Runner.run(agent, input=formatted_input)

    brief = (result.final_output or "").strip()
    if not brief:
        brief = (
            "# Agentic Advances Brief\n\n"
            "_The model returned no output. Try a different topic focus or output depth._"
        )

    write_artifact(folder, "agentic_advances_brief.md", brief)
    write_artifact(folder, "task.txt", formatted_input)

    log.info("agentic_advances.complete folder=%s len=%d", folder, len(brief))

    return AgenticAdvancesResult(
        artifact_folder=folder,
        agentic_advances_brief=brief,
    )
