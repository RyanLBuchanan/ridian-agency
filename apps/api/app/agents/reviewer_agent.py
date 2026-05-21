"""Reviewer Agent — polishes the writer's draft without changing its meaning."""

from agents import Agent

from . import default_model, load_prompt


reviewer_agent = Agent(
    name="Reviewer Agent",
    instructions=load_prompt("reviewer_prompt.txt"),
    model=default_model(),
)
