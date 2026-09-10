import logfire
from pydantic_ai import Agent

from tiger_agent.agent.constants import (
    PROMPT_CACHE_MODEL_SETTINGS,
    SPAM_DETECTION_MODEL,
    SPAM_DETECTION_USAGE_LIMITS,
)
from tiger_agent.agent.tiger_agent import SPAM_DETECTION_PROMPT_REGEX, TigerAgent
from tiger_agent.agent.types import AgentResponseContext, SpamAssessment
from tiger_agent.salesforce.types import CaseData


@logfire.instrument("assess_case_for_spam", extract_args=False)
async def assess_case_for_spam(
    agent: TigerAgent,
    ctx: AgentResponseContext,
    case: CaseData,
) -> SpamAssessment:
    """Decide whether a newly created case is spam.

    Deliberately does not go through ``create_agent_and_context``: spam triage
    needs to read the case, not investigate it, so it runs on a small model with
    no toolsets, no skills and no subagents. The prompt is rendered through the
    normal template machinery so a downstream package can override it.
    """
    system_prompt = await agent.render_prompts(
        regex=SPAM_DETECTION_PROMPT_REGEX, ctx=ctx, extra_ctx={}
    )

    spam_agent = Agent(
        model=SPAM_DETECTION_MODEL,
        model_settings=PROMPT_CACHE_MODEL_SETTINGS,
        output_type=SpamAssessment,
        system_prompt=system_prompt,
        # Default is 1 retry (2 attempts total). Seen in production: the model
        # garbles the structured output the same way on both attempts, so the
        # run fails outright. A larger budget gives it more chances to recover.
        retries=3,
    )

    user_prompt = "\n".join(
        [
            "Assess the following Salesforce case.",
            "",
            f"Case Number: {case.CaseNumber or '(none)'}",
            f"Subject: {case.Subject or '(none)'}",
            f"Origin: {case.Origin or '(unknown)'}",
            f"Supplied Name: {case.SuppliedName or '(none)'}",
            f"Supplied Email: {case.SuppliedEmail or case.ContactEmail or '(none)'}",
            f"Account Id: {case.AccountId or '(none)'}",
            "",
            "Description:",
            case.Description or "(empty)",
        ]
    )

    result = await spam_agent.run(
        user_prompt=user_prompt,
        usage_limits=SPAM_DETECTION_USAGE_LIMITS,
    )
    logfire.info(
        "Spam assessment complete",
        is_spam=result.output.is_spam,
        reason=result.output.reason,
        case_number=case.CaseNumber,
    )
    return result.output
