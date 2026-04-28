from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent, BinaryContent, Tool
from pydantic_ai.messages import UserContent
from pydantic_ai.models.anthropic import AnthropicModel

from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    ExtraContextDict,
)
from tiger_agent.mcp.utils import filter_mcp_servers
from tiger_agent.prompts.utils import format_thread_history
from tiger_agent.salesforce.types import (
    SalesforceBaseEvent,
)
from tiger_agent.slack.types import SlackFile
from tiger_agent.slack.utils import (
    download_slack_hosted_file,
    fetch_bot_info,
    fetch_thread_messages,
    fetch_user_info,
)
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext
from tiger_agent.utils import wrap_mcp_servers_with_exception_handling

if TYPE_CHECKING:
    from tiger_agent.agent.tiger_agent import TigerAgent


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
        mcp_servers=mcp_servers,
    )

    extra_ctx: ExtraContextDict = {}
    await agent.augment_context(ctx=ctx, extra_ctx=extra_ctx)

    if not isinstance(event, SalesforceBaseEvent) and event.thread_ts and hctx.bot_info:
        thread_messages = await fetch_thread_messages(
            client=hctx.app.client,
            channel=event.channel,
            thread_ts=event.thread_ts,
        )
        extra_ctx["thread_history"] = format_thread_history(
            thread_messages, hctx.bot_info, [event.ts]
        )

    system_prompt = await agent.make_system_prompt(ctx=ctx, extra_ctx=extra_ctx)
    user_prompt = await agent.make_user_prompt(ctx=ctx, extra_ctx=extra_ctx)

    toolsets = [mcp_config.mcp_server for mcp_config in mcp_servers.values()]

    async def _download_slack_hosted_file(
        file: SlackFile,
    ) -> BinaryContent | str | None:
        return await download_slack_hosted_file(file=file)

    pydantic_agent = Agent(
        model=agent.model,
        deps_type=dict[str, Any],
        system_prompt=system_prompt,
        output_type=AgentSalesforceResponse
        if isinstance(event, SalesforceBaseEvent)
        else str,
        tools=[
            Tool(
                _download_slack_hosted_file,
                takes_ctx=False,
                name="download_slack_hosted_file",
                description="This will download a file associated with a Slack message and return its contents. Note: only images, text, or PDFs are supported.",
            )
        ],
        toolsets=toolsets,
        model_settings={"extra_headers": {"anthropic-beta": "context-1m-2025-08-07"}}
        if (isinstance(agent.model, str) and agent.model.startswith("anthropic:"))
        or isinstance(agent.model, AnthropicModel)
        else None,
    )

    return AgentAndContext(
        agent=pydantic_agent,
        user_prompt=user_prompt,
        ctx=ctx,
        channel_to_respond=channel_to_respond,
    )
