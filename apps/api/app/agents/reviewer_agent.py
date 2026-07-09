"""Reviewer Agent — polishes the writer's draft without changing its meaning."""

from . import PromptAgent, load_prompt


reviewer_agent = PromptAgent(
    name="Reviewer Agent",
    instructions=load_prompt("reviewer_prompt.txt"),
)
