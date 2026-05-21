"""Presentation Agent — turns the polished document into a slide deck outline."""

from agents import Agent

from . import default_model, load_prompt


presentation_agent = Agent(
    name="Presentation Agent",
    instructions=load_prompt("presentation_prompt.txt"),
    model=default_model(),
)
