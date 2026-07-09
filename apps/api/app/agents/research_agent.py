"""Research Agent — produces a decision-ready market research summary."""

from . import PromptAgent, load_prompt


research_agent = PromptAgent(
    name="Research Agent",
    instructions=load_prompt("research_prompt.txt"),
)
