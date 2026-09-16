from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.messages import UserContent
from pydantic_ai.toolsets.abstract import AbstractToolset
from pydantic_ai_harness import SubAgent, SubAgents
from pydantic_ai_summarization import ContextManagerCapability

from tiger_agent.agent.constants import (
    AGENT_MAX_CONTEXT_TOKENS,
    AGENT_MAX_DELEGATIONS,
    AGENT_MAX_TOOL_OUTPUT_TOKENS,
    LIMITED_PROFILE_MODEL,
    LIMITED_PROFILE_USAGE_LIMITS,
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
from tiger_agent.agent.tools import create_rule_action_tools, create_tools
from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    AssessmentReport,
    ExtraContextDict,
    InvestigationReport,
)
from tiger_agent.db.utils import get_salesforce_account_id_for_channel
from tiger_agent.mcp.types import McpConfig
from tiger_agent.mcp.utils import filter_mcp_servers
from tiger_agent.salesforce.types import (
    ExecutionProfile,
    SalesforceBaseEvent,
    UserDefinedRuleExecution,
)
from tiger_agent.slack.types import SlackBaseEvent
from tiger_agent.slack.utils import (
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
    channel_to_respond: str
    usage_limits: UsageLimits = field(default_factory=lambda: AGENT_USAGE_LIMITS)


def _output_type(event) -> type:
    if isinstance(event, SalesforceBaseEvent):
        return AgentSalesforceResponse
    if isinstance(event, UserDefinedRuleExecution) and event.trigger == "schedule":
        return AssessmentReport
    return str


async def create_agent_and_context(
    hctx: HarnessContext,
    task: Task,
    agent: TigerAgent,
    channel_to_respond: str,
    profile: ExecutionProfile = "full",
    extra_context: ExtraContextDict | None = None,
    subagent_event_handler: EventStreamHandler[Any] | None = None,
) -> AgentAndContext:
    """Build the coordinator agent and the context it runs with.

    Args:
        profile: `full` is the normal agent; `limited` runs a cheaper model with
            only the posting tools, no MCP servers or sub-agents, and a tight
            budget -- enough to relay a notification, not to investigate.
        extra_context: Extra template variables (e.g. the rule being executed).
        subagent_event_handler: Optional pydantic-ai event stream handler that
            receives the events of every delegated sub-agent run (its model
            streaming and tool events), e.g. to surface progress to the user.
    """
    event = task.event
    limited = profile == "limited"

    destination_channel_info = await fetch_channel_info(
        client=hctx.app.client, channel_id=channel_to_respond
    )

    if limited:
        mcp_servers = {}
    else:
        all_mcp_servers = agent.mcp_loader()
        agent.augment_mcp_servers(all_mcp_servers)

        mcp_servers = await filter_mcp_servers(
            mcp_servers=all_mcp_servers,
            client=hctx.app.client,
            channel_id=channel_to_respond,
        )

        wrap_mcp_servers_with_tool_call_guards(mcp_servers=mcp_servers)

    ctx = AgentResponseContext(
        task=task,
        mention=event,
        bot=hctx.bot_info,
        user=await fetch_user_info(client=hctx.app.client, user_id=event.user)
        if isinstance(event, SlackBaseEvent)
        else None,
    )

    extra_ctx: ExtraContextDict = dict(extra_context or {})
    await agent.augment_context(ctx=ctx, extra_ctx=extra_ctx, mcp_servers=mcp_servers)

    if isinstance(event, SlackBaseEvent) and event.thread_ts and hctx.bot_info:
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

    if limited:
        toolsets = []
        tools = create_rule_action_tools(hctx=hctx)
        capabilities = budget_capabilities()
        model = LIMITED_PROFILE_MODEL
        usage_limits = LIMITED_PROFILE_USAGE_LIMITS
    else:
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
        capabilities = [
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
        ]
        model = agent.model
        usage_limits = AGENT_USAGE_LIMITS

    agent = Agent(
        capabilities=capabilities,
        model=model,
        model_settings=PROMPT_CACHE_MODEL_SETTINGS,
        deps_type=dict[str, Any],
        system_prompt=system_prompt,
        output_type=_output_type(event),
        tools=tools,
        toolsets=toolsets,
        retries=5,
    )

    return AgentAndContext(
        agent=agent,
        user_prompt=user_prompt,
        ctx=ctx,
        channel_to_respond=channel_to_respond,
        usage_limits=usage_limits,
    )
