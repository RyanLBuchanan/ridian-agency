"""Triage Agent — orchestrator that exposes the specialist agents as tools.

The default workflow in ``workflow_service`` calls each specialist directly
for deterministic output. This triage agent is the agents-as-tools alternative:
hand it a task and it decides which specialists to call. Useful for ad-hoc
operator requests where the full pipeline is overkill.
"""

from agents import Agent

from . import default_model, load_prompt
from .email_agent import email_agent
from .presentation_agent import presentation_agent
from .research_agent import research_agent
from .reviewer_agent import reviewer_agent
from .writer_agent import writer_agent


TRIAGE_INSTRUCTIONS = """\
You are the Triage Agent for Ridian Agency. You receive operator tasks and
decide which of your specialist tools to call, in what order, to produce the
deliverables the operator needs.

Your tools (each one runs a full specialist agent):
- run_research: market research summary
- run_writer: turns research into a polished business document
- run_reviewer: polishes a document
- run_presentation: turns a document into a slide outline
- run_email: drafts a short outbound email

Default pipeline when the operator wants the full package:
research -> writer -> reviewer -> presentation -> email

Be efficient. Don't call tools you don't need. When you respond to the operator,
include the final deliverables clearly labeled.
"""


triage_agent = Agent(
    name="Triage Agent",
    instructions=TRIAGE_INSTRUCTIONS,
    model=default_model(),
    tools=[
        research_agent.as_tool(
            tool_name="run_research",
            tool_description="Produce a market research summary for the task.",
        ),
        writer_agent.as_tool(
            tool_name="run_writer",
            tool_description="Draft a business document from the research summary.",
        ),
        reviewer_agent.as_tool(
            tool_name="run_reviewer",
            tool_description="Polish a business document without changing its meaning.",
        ),
        presentation_agent.as_tool(
            tool_name="run_presentation",
            tool_description="Produce a slide deck outline from a business document.",
        ),
        email_agent.as_tool(
            tool_name="run_email",
            tool_description="Draft a short outbound email referencing the document.",
        ),
    ],
)
