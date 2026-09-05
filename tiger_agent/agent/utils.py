from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import logfire
from pydantic_ai import Agent
from pydantic_ai.messages import UserContent
from pydantic_ai.toolsets.abstract import AbstractToolset
from pydantic_ai_harness import SubAgent, SubAgents
from pydantic_ai_summarization import ContextManagerCapability

from tiger_agent.agent.constants import (
    CASE_SUMMARY_MODEL,
    SPAM_DETECTION_MODEL,
    SPAM_DETECTION_USAGE_LIMITS,
)
from tiger_agent.agent.tiger_agent import (
    INVESTIGATOR_SYSTEM_PROMPT_REGEX,
    SPAM_DETECTION_PROMPT_REGEX,
    TigerAgent,
)
from tiger_agent.agent.tools import create_tools
from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    CaseSummary,
    ExtraContextDict,
    SpamAssessment,
)
from tiger_agent.db.utils import get_salesforce_account_id_for_channel
from tiger_agent.mcp.types import McpConfig
from tiger_agent.mcp.utils import filter_mcp_servers
from tiger_agent.salesforce.types import (
    CaseData,
    SalesforceBaseEvent,
)
from tiger_agent.slack.utils import (
    fetch_channel_info,
    fetch_thread_messages,
    fetch_user_info,
)
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext
from tiger_agent.utils import (
    pretty_print_models,
    wrap_mcp_servers_with_exception_handling,
)

# Only the settings matching the active model's provider prefix are read; the rest are
# ignored, so it's safe to set both Anthropic's and OpenRouter's cache keys regardless of
# whether `model` is an `anthropic:` or `openrouter:` model string.
PROMPT_CACHE_MODEL_SETTINGS: dict[str, Any] = {
    "anthropic_cache_instructions": True,
    "anthropic_cache_tool_definitions": True,
    "openrouter_cache_instructions": True,
    "openrouter_cache_tool_definitions": True,
}


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


def _build_toolset(mcp_config: McpConfig) -> AbstractToolset:
    """Wrap an McpConfig's toolset with tool-name filtering and prefixing."""
    toolset: AbstractToolset = mcp_config.mcp_server
    if mcp_config.allowed_tools:
        allowed = set(mcp_config.allowed_tools)
        toolset = toolset.filtered(lambda _ctx, tool_def: tool_def.name in allowed)
    if mcp_config.tool_prefix:
        toolset = toolset.prefixed(mcp_config.tool_prefix)
    return toolset


@dataclass
class AgentAndContext:
    agent: Agent
    user_prompt: str | Sequence[UserContent]
    ctx: AgentResponseContext
    channel_to_respond: str


async def create_agent_and_context(
    hctx: HarnessContext,
    task: Task,
    agent: TigerAgent,
    channel_to_respond: str,
) -> AgentAndContext:
    event = task.event

    destination_channel_info = await fetch_channel_info(
        client=hctx.app.client, channel_id=channel_to_respond
    )

    all_mcp_servers = agent.mcp_loader()
    agent.augment_mcp_servers(all_mcp_servers)

    mcp_servers = await filter_mcp_servers(
        mcp_servers=all_mcp_servers,
        client=hctx.app.client,
        channel_id=channel_to_respond,
    )

    wrap_mcp_servers_with_exception_handling(mcp_servers=mcp_servers)

    ctx = AgentResponseContext(
        task=task,
        mention=event,
        bot=hctx.bot_info,
        user=await fetch_user_info(client=hctx.app.client, user_id=event.user)
        if not isinstance(event, SalesforceBaseEvent)
        else None,
    )

    extra_ctx: ExtraContextDict = {}
    await agent.augment_context(ctx=ctx, extra_ctx=extra_ctx, mcp_servers=mcp_servers)

    if not isinstance(event, SalesforceBaseEvent) and event.thread_ts and hctx.bot_info:
        thread_messages = await fetch_thread_messages(
            client=hctx.app.client,
            channel=event.channel,
            thread_ts=event.thread_ts,
        )

        extra_ctx["thread_history"] = pretty_print_models(thread_messages)

    system_prompt = await agent.make_system_prompt(ctx=ctx, extra_ctx=extra_ctx)
    user_prompt = await agent.make_user_prompt(ctx=ctx, extra_ctx=extra_ctx)
    investigator_prompt = await agent.render_prompts(
        ctx=ctx, extra_ctx=extra_ctx, regex=INVESTIGATOR_SYSTEM_PROMPT_REGEX
    )

    toolsets = [_build_toolset(mcp_config) for mcp_config in mcp_servers.values()]
    channel_is_linked_to_salesforce_account = bool(
        await get_salesforce_account_id_for_channel(
            pool=hctx.pool, channel_id=channel_to_respond
        )
    )
    tools = create_tools(
        hctx=hctx,
        task=task,
        channel_info=destination_channel_info,
        channel_is_linked_to_salesforce_account=channel_is_linked_to_salesforce_account,
    )

    agent = Agent(
        capabilities=[
            ContextManagerCapability(
                max_tokens=800_000,
                max_tool_output_tokens=50_000,
            ),
            SubAgents(
                agents=[
                    SubAgent(
                        Agent(
                            model=agent.model,
                            model_settings=PROMPT_CACHE_MODEL_SETTINGS,
                            name="investigator",
                            description=(
                                "Delegate a self-contained investigation that would require "
                                "3+ tool calls, iterative probing, or any tool whose parameters "
                                "are open-ended query DSLs (PromQL/Thanos metric queries, "
                                "Elasticsearch/log-search queries, SQL against catalog or "
                                "analytics, savannah_client::* tools, hybrid Slack search) — "
                                "these iterate on syntax and return large payloads that will "
                                "clutter your context. Also delegate skill workflows with "
                                "independent sections (fan them out in parallel, one "
                                "delegate_task per section). DO NOT delegate: a single "
                                "structured lookup by known ID (get_case_details, "
                                "get_account_details, get_releases, fetch by permalink), "
                                "one-shot searches whose result is your final answer, or "
                                "questions already answered by data in your context. Phrase "
                                "the task as one specific question and include every "
                                "identifier the investigator will need (service_id, "
                                "project_id, case_id/number, account_id, user email, time "
                                "window). The investigator returns a distilled answer with "
                                "evidence, not raw tool output."
                            ),
                            deps_type=dict[str, Any],
                            system_prompt=investigator_prompt,
                            capabilities=[
                                ContextManagerCapability(
                                    max_tokens=800_000,
                                    max_tool_output_tokens=50_000,
                                ),
                            ],
                        )
                    )
                ],
                inherit_tools=True,
            ),
        ],
        model=agent.model,
        model_settings=PROMPT_CACHE_MODEL_SETTINGS,
        deps_type=dict[str, Any],
        system_prompt=system_prompt,
        output_type=AgentSalesforceResponse
        if isinstance(event, SalesforceBaseEvent)
        else str,
        tools=tools,
        toolsets=toolsets,
        retries=5,
    )

    return AgentAndContext(
        agent=agent,
        user_prompt=user_prompt,
        ctx=ctx,
        channel_to_respond=channel_to_respond,
    )
