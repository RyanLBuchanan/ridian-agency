"""Writer Agent — turns the research summary into a polished business document."""

from agents import Agent

from . import default_model, load_prompt


writer_agent = Agent(
    name="Writer Agent",
    instructions=load_prompt("writer_prompt.txt"),
    model=default_model(),
)
