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

from tiger_agent.agent.tiger_agent import INVESTIGATOR_SYSTEM_PROMPT_REGEX, TigerAgent
from tiger_agent.agent.tools import create_tools
from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    CaseSummary,
    ExtraContextDict,
)
from tiger_agent.mcp.types import McpConfig
from tiger_agent.mcp.utils import filter_mcp_servers
from tiger_agent.salesforce.types import (
    SalesforceBaseEvent,
)
from tiger_agent.slack.utils import (
    fetch_bot_info,
    fetch_thread_messages,
    fetch_user_info,
)
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext
from tiger_agent.utils import (
    pretty_print_models,
    wrap_mcp_servers_with_exception_handling,
)

CASE_SUMMARY_MODEL = "anthropic:claude-sonnet-4-6"


@logfire.instrument("summarize_new_case", extract_args=False)
async def summarize_new_case(subject: str, description: str) -> str:
    agent = Agent(
        model=CASE_SUMMARY_MODEL,
        output_type=CaseSummary,
        system_prompt=(
            "You summarize customer-submitted support case descriptions into a brief, "
            "neutral 1-2 sentence summary of the issue. Do not add speculation, "
            "greetings, or next steps."
        ),
    )
    result = await agent.run(f"Subject: {subject}\n\nDescription: {description}")
    return result.output.short_description


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

    if not hctx.bot_info:
        hctx.bot_info = await fetch_bot_info(hctx.app.client)

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
    tools = create_tools(hctx=hctx, task=task)

    investigator = SubAgent(
        Agent(
            model=agent.model,
            name="investigator",
            description=(
                "Delegate a self-contained investigation question. Use this when "
                "answering would require multiple tool calls that would otherwise "
                "clutter your own context — metric probing, log searches, query "
                "analytics, schema discovery, historical lookups. Phrase the task "
                "as a single specific question and include any identifiers the "
                "investigator will need (service_id, project_id, case number, "
                "user email, time window). The investigator returns a distilled "
                "answer with evidence, not raw tool output."
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

    pydantic_agent = Agent(
        capabilities=[
            ContextManagerCapability(
                max_tokens=800_000,
                max_tool_output_tokens=50_000,
            ),
            SubAgents(
                agents=[investigator],
                inherit_tools=True,
            ),
        ],
        model=agent.model,
        deps_type=dict[str, Any],
        system_prompt=system_prompt,
        output_type=AgentSalesforceResponse
        if isinstance(event, SalesforceBaseEvent)
        else str,
        tools=tools,
        toolsets=toolsets,
    )

    return AgentAndContext(
        agent=pydantic_agent,
        user_prompt=user_prompt,
        ctx=ctx,
        channel_to_respond=channel_to_respond,
    )
