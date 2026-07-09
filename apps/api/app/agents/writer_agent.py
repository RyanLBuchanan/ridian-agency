"""Writer Agent — turns the research summary into a polished business document."""

from . import PromptAgent, load_prompt


writer_agent = PromptAgent(
    name="Writer Agent",
    instructions=load_prompt("writer_prompt.txt"),
)
