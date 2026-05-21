"""Research Agent — produces a decision-ready market research summary."""

from datetime import date

from agents import Agent, function_tool

from . import default_model, load_prompt


@function_tool
def get_today() -> str:
    """Return today's date in ISO format. Useful for grounding research in 'now'."""
    return date.today().isoformat()


research_agent = Agent(
    name="Research Agent",
    instructions=load_prompt("research_prompt.txt"),
    model=default_model(),
    tools=[get_today],
)
