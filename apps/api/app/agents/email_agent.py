"""Email Agent — drafts a short outbound email referencing the document."""

from agents import Agent

from . import default_model, load_prompt


email_agent = Agent(
    name="Email Agent",
    instructions=load_prompt("email_prompt.txt"),
    model=default_model(),
)
