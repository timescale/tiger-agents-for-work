from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from psycopg_pool import AsyncConnectionPool
from pydantic_ai import Agent, Tool
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.messages import UserContent
from pydantic_ai.toolsets.abstract import AbstractToolset
from pydantic_ai_harness import SubAgent, SubAgents
from pydantic_ai_summarization import ContextManagerCapability
from slack_sdk.web.async_client import AsyncWebClient

from tiger_agent.agent.constants import (
    AGENT_MAX_CONTEXT_TOKENS,
    AGENT_MAX_DELEGATIONS,
    AGENT_MAX_TOOL_OUTPUT_TOKENS,
    PROMPT_CACHE_MODEL_SETTINGS,
)
from tiger_agent.agent.limits import (
    AGENT_USAGE_LIMITS,
    FINALIZE_PROMPT_INVESTIGATOR,
    make_limit_warner,
)
from tiger_agent.agent.partial_agent import PartialAnswerAgent
from tiger_agent.agent.tiger_agent import (
    INVESTIGATOR_SYSTEM_PROMPT_REGEX,
    TigerAgent,
)
from tiger_agent.agent.tools import create_tools
from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    ExtraContextDict,
    InvestigationReport,
    LinkedChannelInfo,
)
from tiger_agent.customer.types import CustomerQuestionEvent
from tiger_agent.db.utils import get_salesforce_account_id_for_channel
from tiger_agent.mcp.types import McpConfig
from tiger_agent.mcp.utils import filter_mcp_servers
from tiger_agent.salesforce.types import SalesforceBaseEvent
from tiger_agent.slack.types import BotInfo, SlackBaseEvent, UserInfo
from tiger_agent.slack.utils import (
    channel_is_external,
    fetch_channel_info,
    fetch_thread_messages,
    fetch_user_info,
)
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext
from tiger_agent.utils import (
    pretty_print_models,
    wrap_mcp_servers_with_tool_call_guards,
)


def _build_toolset(mcp_config: McpConfig) -> AbstractToolset:
    """Wrap an McpConfig's toolset with tool-name filtering and prefixing."""
    toolset: AbstractToolset = mcp_config.mcp_server
    if mcp_config.allowed_tools:
        allowed = set(mcp_config.allowed_tools)
        toolset = toolset.filtered(lambda _ctx, tool_def: tool_def.name in allowed)
    if mcp_config.tool_prefix:
        toolset = toolset.prefixed(mcp_config.tool_prefix)
    return toolset


def budget_capabilities() -> list:
    """Context management plus the approaching-limit warner, for one agent.

    Built per call: both capabilities hold per-agent state, so the coordinator
    and each delegate need their own instances, not a shared list.
    """
    return [
        ContextManagerCapability(
            max_tokens=AGENT_MAX_CONTEXT_TOKENS,
            max_tool_output_tokens=AGENT_MAX_TOOL_OUTPUT_TOKENS,
        ),
        make_limit_warner(),
    ]


def build_investigator(model, system_prompt: str) -> SubAgent:
    """The delegate a coordinating agent gets, on its own request budget.

    `usage_limits` switches the harness to isolated accounting, so the child's
    AGENT_MAX_REQUESTS are its own and exhausting them cannot end the parent's
    run. `PartialAnswerAgent` turns that exhaustion into a finalized report
    rather than the harness's one-line "reached its usage budget".
    """
    return SubAgent(
        PartialAnswerAgent(
            Agent(
                model=model,
                model_settings=PROMPT_CACHE_MODEL_SETTINGS,
                name="investigator",
                description=(
                    "Delegate a self-contained investigation that would require "
                    "3+ tool calls, iterative probing, or any tool whose parameters "
                    "are open-ended query languages (metric queries, log searches, "
                    "SQL, hybrid or semantic search) — these iterate on syntax and "
                    "return large payloads that will clutter your context. Also "
                    "delegate skill workflows with independent sections (fan them "
                    "out in parallel, one delegate_task per section). DO NOT "
                    "delegate: a single structured lookup by a known identifier, "
                    "one-shot searches whose result is your final answer, or "
                    "questions already answered by data in your context. Phrase "
                    "the task as one specific question and include every "
                    "identifier the investigator will need and the time window, "
                    "plus the facts you have already established and anything "
                    "out of scope. The investigator returns a report: "
                    "answer, evidence, confidence, completed_steps, and "
                    "dropped_steps — work it could not finish, with what it "
                    "tried. Decide what to do with every dropped step: "
                    "re-delegate it as its own task (once), answer it yourself, "
                    "or record it as a gap."
                ),
                deps_type=dict[str, Any],
                system_prompt=system_prompt,
                output_type=InvestigationReport,
                capabilities=budget_capabilities(),
            ),
            finalize_prompt=FINALIZE_PROMPT_INVESTIGATOR,
        ),
        usage_limits=AGENT_USAGE_LIMITS,
        max_calls=AGENT_MAX_DELEGATIONS,
    )


@dataclass
class AgentAndContext:
    agent: Agent
    user_prompt: str | Sequence[UserContent]
    ctx: AgentResponseContext


def destination_channel_of(event: Any) -> str | None:
    """The Slack channel a run for `event` posts to, fixed when it was enqueued.

    See `HasDestinationChannel`. Events that never reach the coordinator have no
    such attribute; `None` means the run has no Slack destination.
    """
    return getattr(event, "destination_channel", None)


def _output_type(event: Any) -> type:
    return AgentSalesforceResponse if isinstance(event, SalesforceBaseEvent) else str


async def build_agent_and_context(
    *,
    agent: TigerAgent,
    task: Task,
    bot: BotInfo,
    internal_only: bool,
    user: UserInfo | None = None,
    tools: Sequence[Tool] = (),
    extra_context: ExtraContextDict | None = None,
    subagent_event_handler: EventStreamHandler[Any] | None = None,
    output_type: type | None = None,
) -> AgentAndContext:
    """Assemble the coordinator from `agent`'s prompts and MCP servers.

    Needs neither Slack nor the database: everything channel- or user-specific
    arrives through `user`, `tools` and `extra_context`. `create_agent_and_context`
    is the production wrapper that supplies them from a `HarnessContext`; the
    eval suite calls this directly with `internal_only=False`.

    Args:
        internal_only: True when the audience is internal, so MCP servers marked
            `internal_only` may be loaded. False for anything a customer sees.
        user: The Slack user behind the event, when there is one.
        tools: Slack-side tools for the run (see `create_tools`).
        extra_context: Extra template variables, e.g. the thread history.
        subagent_event_handler: Optional pydantic-ai event stream handler that
            receives the events of every delegated sub-agent run (its model
            streaming and tool events), e.g. to surface progress to the user.
        output_type: Overrides the output type derived from the event.
    """
    event = task.event

    all_mcp_servers = agent.mcp_loader()
    agent.augment_mcp_servers(all_mcp_servers)
    mcp_servers = await filter_mcp_servers(
        mcp_servers=all_mcp_servers, include_internal=internal_only
    )
    wrap_mcp_servers_with_tool_call_guards(mcp_servers=mcp_servers)

    ctx = AgentResponseContext(task=task, mention=event, bot=bot, user=user)

    extra_ctx: ExtraContextDict = dict(extra_context or {})
    await agent.augment_context(ctx=ctx, extra_ctx=extra_ctx, mcp_servers=mcp_servers)

    system_prompt = await agent.make_system_prompt(ctx=ctx, extra_ctx=extra_ctx)
    user_prompt = await agent.make_user_prompt(ctx=ctx, extra_ctx=extra_ctx)
    investigator_prompt = await agent.render_prompts(
        ctx=ctx, extra_ctx=extra_ctx, regex=INVESTIGATOR_SYSTEM_PROMPT_REGEX
    )

    coordinator = Agent(
        capabilities=[
            *budget_capabilities(),
            SubAgents(
                agents=[
                    build_investigator(
                        model=agent.model, system_prompt=investigator_prompt
                    )
                ],
                inherit_tools=True,
                event_stream_handler=subagent_event_handler,
            ),
        ],
        model=agent.model,
        model_settings=PROMPT_CACHE_MODEL_SETTINGS,
        deps_type=dict[str, Any],
        system_prompt=system_prompt,
        output_type=output_type or _output_type(event),
        tools=list(tools),
        toolsets=[_build_toolset(mcp_config) for mcp_config in mcp_servers.values()],
        retries=5,
    )

    return AgentAndContext(agent=coordinator, user_prompt=user_prompt, ctx=ctx)


async def create_agent_and_context(
    hctx: HarnessContext,
    task: Task,
    agent: TigerAgent,
    subagent_event_handler: EventStreamHandler[Any] | None = None,
) -> AgentAndContext:
    """Build the coordinator for a task the harness is handling.

    The destination channel comes from the event (`destination_channel`, set
    when it was enqueued), never from the caller. Whether internal-only MCP
    servers and tools may be used is decided here: never for a
    `CustomerQuestionEvent`, and not when the destination channel is shared
    with external users.
    """
    event = task.event
    destination = destination_channel_of(event)

    channel_info: LinkedChannelInfo | None = None
    if destination is not None:
        channel_info = await fetch_linked_channel_info(
            client=hctx.app.client, pool=hctx.pool, channel_id=destination
        )
        if channel_info is None:
            # fetch_channel_info swallows Slack errors. A run that cannot see
            # the channel it must post to is misconfigured (bot not a member,
            # bad id); fail here so the task retries and the error is visible,
            # rather than answering with a quietly reduced tool set.
            raise RuntimeError(
                f"Could not read Slack channel {destination!r} for the run's destination"
            )

    internal_only = not isinstance(event, CustomerQuestionEvent) and not (
        channel_info is not None and channel_is_external(channel_info)
    )

    user: UserInfo | None = None
    extra_context: ExtraContextDict = {}
    if isinstance(event, SlackBaseEvent):
        user = await fetch_user_info(client=hctx.app.client, user_id=event.user)
        if event.thread_ts and hctx.bot_info:
            thread_messages = await fetch_thread_messages(
                client=hctx.app.client,
                channel=event.channel,
                thread_ts=event.thread_ts,
            )
            extra_context["thread_history"] = pretty_print_models(thread_messages)

    return await build_agent_and_context(
        agent=agent,
        task=task,
        bot=hctx.bot_info,
        internal_only=internal_only,
        user=user,
        tools=create_tools(hctx=hctx, task=task, channel_info=channel_info),
        extra_context=extra_context,
        subagent_event_handler=subagent_event_handler,
    )


async def fetch_linked_channel_info(
    client: AsyncWebClient, pool: AsyncConnectionPool, channel_id: str
) -> LinkedChannelInfo | None:
    channel_info, account_id = await asyncio.gather(
        fetch_channel_info(client=client, channel_id=channel_id),
        get_salesforce_account_id_for_channel(pool=pool, channel_id=channel_id),
    )
    if channel_info is None:
        return None
    return LinkedChannelInfo(
        **channel_info.model_dump(), linked_salesforce_account_id=account_id
    )
