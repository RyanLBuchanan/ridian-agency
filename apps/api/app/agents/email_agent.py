"""Email Agent — drafts a short outbound email referencing the document."""

from . import PromptAgent, load_prompt


email_agent = PromptAgent(
    name="Email Agent",
    instructions=load_prompt("email_prompt.txt"),
)
