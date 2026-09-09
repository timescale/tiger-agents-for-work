import logfire
from pydantic_ai import Agent

from tiger_agent.agent.constants import (
    CASE_SUMMARY_MODEL,
    PROMPT_CACHE_MODEL_SETTINGS,
)
from tiger_agent.agent.types import CaseSummary


@logfire.instrument("summarize_new_case", extract_args=False)
async def summarize_new_case(subject: str, description: str) -> str:
    agent = Agent(
        model=CASE_SUMMARY_MODEL,
        model_settings=PROMPT_CACHE_MODEL_SETTINGS,
        output_type=CaseSummary,
        system_prompt=(
            "You summarize customer-submitted support case descriptions into a brief, "
            "neutral 1-2 sentence summary of the issue. Do not add speculation, "
            "greetings, or next steps."
        ),
    )
    result = await agent.run(f"Subject: {subject}\n\nDescription: {description}")
    return result.output.short_description
