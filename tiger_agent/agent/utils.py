from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai import Agent, BinaryContent, Tool
from pydantic_ai.messages import UserContent
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel

from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    ExtraContextDict,
)
from tiger_agent.db.utils import (
    delete_user_defined_rule,
    insert_user_defined_rule,
    list_user_defined_rules,
)
from tiger_agent.events import EVENT_TYPE_REGISTRY
from tiger_agent.logfire.constants import LOGFIRE_READ_TOKEN
from tiger_agent.logfire.utils import get_tool_calls_for_event
from tiger_agent.mcp.utils import filter_mcp_servers
from tiger_agent.salesforce.types import (
    SalesforceBaseEvent,
    UserDefinedRule,
)
from tiger_agent.salesforce.utils import (
    EXT_TO_MIME,
    download_content_version_url,
)
from tiger_agent.slack.types import SlackBaseEvent, SlackFile
from tiger_agent.slack.utils import (
    download_slack_hosted_file,
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

if TYPE_CHECKING:
    from tiger_agent.agent.tiger_agent import TigerAgent

# Models that only get the 1M context window via the beta header. Newer models
# (Sonnet 4.6+, Opus 4.6+) include 1M context at standard pricing and don't
# need it; on these older models the header bills requests over 200K input
# tokens at premium long-context rates, so only send it where it does something.
LONG_CONTEXT_BETA_MODELS = ("claude-sonnet-4-5", "claude-sonnet-4-2025")


def build_model_settings(
    model: Model | str | None,
    anthropic_cache_ttl: Literal["5m", "1h"] | None,
) -> dict[str, Any] | None:
    """Assemble Anthropic-specific model settings (prompt caching, 1M beta header)."""
    is_anthropic = (
        isinstance(model, str) and model.startswith("anthropic:")
    ) or isinstance(model, AnthropicModel)
    if not is_anthropic:
        return None

    model_settings: dict[str, Any] = {}
    if anthropic_cache_ttl is not None:
        # Cache tool definitions, system prompt, and message history so
        # each iteration of the agentic loop re-reads them at ~0.1x input
        # price instead of full price (5m writes cost 1.25x, so caching
        # pays for itself after a single read).
        model_settings.update(
            anthropic_cache_tool_definitions=anthropic_cache_ttl,
            anthropic_cache_instructions=anthropic_cache_ttl,
            anthropic_cache_messages=anthropic_cache_ttl,
        )
    model_name = model if isinstance(model, str) else model.model_name
    if any(family in model_name for family in LONG_CONTEXT_BETA_MODELS):
        model_settings["extra_headers"] = {"anthropic-beta": "context-1m-2025-08-07"}
    return model_settings or None


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

    wrap_mcp_servers_with_exception_handling(
        mcp_servers=mcp_servers, compress_tool_results=agent.compress_tool_results
    )

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

        extra_ctx["thread_history"] = pretty_print_models(thread_messages)

    system_prompt = await agent.make_system_prompt(ctx=ctx, extra_ctx=extra_ctx)
    user_prompt = await agent.make_user_prompt(ctx=ctx, extra_ctx=extra_ctx)

    toolsets = [mcp_config.mcp_server for mcp_config in mcp_servers.values()]

    async def _download_slack_hosted_file(
        file: SlackFile,
    ) -> BinaryContent | str | None:
        return await download_slack_hosted_file(file=file)

    def _download_salesforce_hosted_file(
        url: str, filename: str
    ) -> BinaryContent | str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        media_type = EXT_TO_MIME.get(ext, "application/octet-stream")
        try:
            content = download_content_version_url(hctx.salesforce_client, url)
            return BinaryContent(data=content, media_type=media_type)
        except Exception as e:
            return f"Failed to download file: {e}"

    _event_type_by_name: dict[str, type] = {
        cls.__name__: cls for cls in EVENT_TYPE_REGISTRY
    }

    async def _list_user_defined_rules() -> list[UserDefinedRule]:
        assert isinstance(event, SlackBaseEvent)
        return await list_user_defined_rules(pool=hctx.pool, owner_slack_id=event.user)

    async def _delete_user_defined_rule(rule_id: int) -> bool:
        assert isinstance(event, SlackBaseEvent)
        return await delete_user_defined_rule(
            pool=hctx.pool, rule_id=rule_id, owner_slack_id=event.user
        )

    async def _create_user_defined_rule(
        name: str,
        event_type: str,
        criteria: str,
        action_prompt: str,
        criteria_examples: list[str] | None = None,
    ) -> UserDefinedRule:
        assert isinstance(event, SlackBaseEvent)
        if event_type not in _event_type_by_name:
            raise ValueError(
                f"Unknown event_type {event_type!r}. "
                f"Valid options: {', '.join(_event_type_by_name)}"
            )
        cls = _event_type_by_name[event_type]
        subtype_field = cls.model_fields.get("subtype")
        event_subtype = (
            subtype_field.default
            if subtype_field and isinstance(subtype_field.default, str)
            else None
        )
        return await insert_user_defined_rule(
            pool=hctx.pool,
            name=name,
            owner_slack_id=event.user,
            event_type=cls.model_fields["type"].default,
            event_subtype=event_subtype,
            criteria=criteria,
            action_prompt=action_prompt,
            criteria_examples=criteria_examples,
        )

    # just a wrapper so we can pass the event in closure
    async def _get_tool_calls_for_event(
        lookback_hours: float = 24.0,
    ) -> list[dict[str, any]] | None:
        return await get_tool_calls_for_event(
            event=event, lookback_hours=lookback_hours
        )

    event_type_options = "\n".join(
        f"- {cls.__name__}: {cls.event_description}" for cls in EVENT_TYPE_REGISTRY
    )

    tools = [
        Tool(
            _download_slack_hosted_file,
            takes_ctx=False,
            name="download_slack_hosted_file",
            description="This will download a file associated with a Slack message and return its contents. Note: only images, text, or PDFs are supported.",
        ),
        Tool(
            _download_salesforce_hosted_file,
            takes_ctx=False,
            name="download_salesforce_hosted_file",
            description=(
                "Download a Salesforce-hosted file by its relative URL and filename. "
                "Use this for inline images in EmailMessage HtmlBody (e.g. <img src='/sfc/servlet.shepherd/version/download/<id>' alt='filename.png'>). "
                "Pass the src as url and the alt attribute value as filename."
            ),
        ),
        *(
            [
                Tool(
                    _list_user_defined_rules,
                    takes_ctx=False,
                    name="list_user_defined_rules",
                    description=(
                        "List all user-defined rules owned by the current user. "
                        'Use when the user asks things like "show me my rules", '
                        '"what rules do I have set up?", or "list my custom rules".'
                    ),
                ),
                Tool(
                    _delete_user_defined_rule,
                    takes_ctx=False,
                    name="delete_user_defined_rule",
                    description=(
                        "Delete a user-defined rule by its ID. Only rules owned by the current user "
                        "can be deleted. Returns True if deleted, False if not found. Use when the user asks things like "
                        '"delete rule 3", "remove my rule with ID 7", or "turn off rule 12".'
                    ),
                ),
                Tool(
                    _create_user_defined_rule,
                    takes_ctx=False,
                    name="create_user_defined_rule",
                    description=(
                        "Call this when the user wants to be notified, alerted, or asks to create a rule or automation. "
                        "Creates a persistent rule that triggers a custom action when a matching event occurs. "
                        "Infer all parameters from the user's request.\n"
                        f"event_type must be one of:\n{event_type_options}\n"
                        "criteria_examples are optional but improve matching accuracy."
                    ),
                ),
            ]
            if isinstance(event, SlackBaseEvent)
            else []
        ),
    ]

    if LOGFIRE_READ_TOKEN:
        tools.append(
            Tool(
                _get_tool_calls_for_event,
                takes_ctx=False,
                name="get_tool_calls_for_event",
                description=(
                    "Retrieve all tool calls made by the agent in response to the current Slack event. "
                    "Returns a JSON list of tool calls with their names, arguments, and responses. "
                    'Use when the user asks things like "what tools did you call?", '
                    '"what did you look up?", or "show me what you did last time".'
                ),
            )
        )

    pydantic_agent = Agent(
        model=agent.model,
        deps_type=dict[str, Any],
        system_prompt=system_prompt,
        output_type=AgentSalesforceResponse
        if isinstance(event, SalesforceBaseEvent)
        else str,
        tools=tools,
        toolsets=toolsets,
        model_settings=build_model_settings(agent.model, agent.anthropic_cache_ttl),
    )

    return AgentAndContext(
        agent=pydantic_agent,
        user_prompt=user_prompt,
        ctx=ctx,
        channel_to_respond=channel_to_respond,
    )
