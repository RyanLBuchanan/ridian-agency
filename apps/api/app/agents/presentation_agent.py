"""Presentation Agent — turns the polished document into a slide deck outline."""

from . import PromptAgent, load_prompt


presentation_agent = PromptAgent(
    name="Presentation Agent",
    instructions=load_prompt("presentation_prompt.txt"),
)
