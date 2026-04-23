from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, BinaryContent, Tool
from pydantic_ai.messages import UserContent
from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.models.anthropic import AnthropicModel

from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    ExtraContextDict,
)
from tiger_agent.mcp.types import MCPDict
from tiger_agent.mcp.utils import filter_mcp_servers
from tiger_agent.prompts.utils import format_thread_history
from tiger_agent.salesforce.types import (
    SalesforceBaseEvent,
)
from tiger_agent.slack.types import BotInfo, SlackFile
from tiger_agent.slack.utils import (
    download_slack_hosted_file,
    fetch_bot_info,
    fetch_thread_messages,
    fetch_user_info,
)
from tiger_agent.tasks.types import Task, TaskContext
from tiger_agent.utils import wrap_mcp_servers_with_exception_handling


@dataclass
class AgentAndContext:
    agent: Agent
    user_prompt: str | Sequence[UserContent]
    ctx: AgentResponseContext
    channel_to_respond: str


async def create_agent_and_context(
    hctx: TaskContext,
    task: Task,
    model: Model | KnownModelName | str | None,
    bot_info: BotInfo | None,
    channel_to_respond: str,
    mcp_loader: Callable[[], MCPDict],
    augment_mcp_servers: Callable[[MCPDict], None],
    augment_context: Callable[
        [AgentResponseContext, ExtraContextDict], Coroutine[Any, Any, None]
    ],
    make_system_prompt: Callable[
        [AgentResponseContext, ExtraContextDict],
        Coroutine[Any, Any, str | Sequence[str]],
    ],
    make_user_prompt: Callable[
        [AgentResponseContext, ExtraContextDict],
        Coroutine[Any, Any, str | Sequence[UserContent]],
    ],
) -> tuple[AgentAndContext, BotInfo]:
    event = task.event

    if not bot_info:
        bot_info = await fetch_bot_info(hctx.app.client)

    all_mcp_servers = mcp_loader()
    augment_mcp_servers(all_mcp_servers)

    mcp_servers = await filter_mcp_servers(
        mcp_servers=all_mcp_servers,
        client=hctx.app.client,
        channel_id=channel_to_respond,
    )

    wrap_mcp_servers_with_exception_handling(mcp_servers=mcp_servers)

    ctx = AgentResponseContext(
        task=task,
        mention=event,
        bot=bot_info,
        user=await fetch_user_info(client=hctx.app.client, user_id=event.user)
        if not isinstance(event, SalesforceBaseEvent)
        else None,
        mcp_servers=mcp_servers,
        slack_bot_token=hctx.slack_bot_token,
    )

    extra_ctx: ExtraContextDict = {}
    await augment_context(ctx=ctx, extra_ctx=extra_ctx)

    if not isinstance(event, SalesforceBaseEvent) and event.thread_ts and bot_info:
        thread_messages = await fetch_thread_messages(
            client=hctx.app.client,
            channel=event.channel,
            thread_ts=event.thread_ts,
        )
        extra_ctx["thread_history"] = format_thread_history(
            thread_messages, bot_info, [event.ts]
        )

    system_prompt = await make_system_prompt(ctx=ctx, extra_ctx=extra_ctx)
    user_prompt = await make_user_prompt(ctx=ctx, extra_ctx=extra_ctx)

    toolsets = [mcp_config.mcp_server for mcp_config in mcp_servers.values()]

    async def _download_slack_hosted_file(
        file: SlackFile,
    ) -> BinaryContent | str | None:
        return await download_slack_hosted_file(
            file=file, slack_bot_token=hctx.slack_bot_token
        )

    agent = Agent(
        model=model,
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
        if (isinstance(model, str) and model.startswith("anthropic:"))
        or isinstance(model, AnthropicModel)
        else None,
    )

    return AgentAndContext(
        agent=agent,
        user_prompt=user_prompt,
        ctx=ctx,
        channel_to_respond=channel_to_respond,
    ), bot_info
