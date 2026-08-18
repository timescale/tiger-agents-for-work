from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import logfire
from pydantic_ai import Agent, BinaryContent, Tool
from pydantic_ai.messages import UserContent
from pydantic_ai.toolsets.abstract import AbstractToolset
from pydantic_ai_summarization import ContextManagerCapability

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    CaseSummary,
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
from tiger_agent.mcp.types import McpConfig
from tiger_agent.mcp.utils import filter_mcp_servers
from tiger_agent.org_calendar.utils import get_calender_events
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
    find_user_group,
    get_user_ids_in_channel,
    get_user_ids_in_user_group,
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

    toolsets = [_build_toolset(mcp_config) for mcp_config in mcp_servers.values()]

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

    async def _get_user_ids_in_user_group(group_name_or_id: str) -> list[str] | str:
        is_group_id = re.match(r"^S[0-9A-Z]{10}$", group_name_or_id)

        group_id: str | None = group_name_or_id if is_group_id else None

        if not is_group_id:
            group = await find_user_group(
                client=hctx.app.client, user_group_name=group_name_or_id
            )

            if not group:
                return f"Could not find group matching [{group_name_or_id}]"

            group_id = group.id

        return await get_user_ids_in_user_group(
            client=hctx.app.client, user_group_id=group_id
        )

    async def _get_user_ids_in_channel(channel_id: str) -> list[str] | str:
        if not re.fullmatch(r"C[0-9A-Z]{10}", channel_id):
            return (
                f"Invalid channel id [{channel_id}]. Expected format: "
                "'C' followed by 10 uppercase alphanumeric characters (e.g. 'C0123456789')."
            )
        return await get_user_ids_in_channel(
            client=hctx.app.client, channel_id=channel_id
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
        Tool(
            get_calender_events,
            takes_ctx=False,
            name="get_org_calendar_events",
            description=(
                "Fetch events from the organization's shared calendar (Justworks feed) "
                "between two datetimes. Returns a list of CalenderEvent objects with "
                "`summary`, `start`, `end`, and `type` fields.\n\n"
                "The `type` field is one of:\n"
                "- 'payday' — company paydays / payroll dates\n"
                "- 'pto' — an employee is out of office (vacation, sick, personal, etc.)\n\n"
                "Use the optional `events_to_filter` argument to restrict results to "
                "one or more of those types. Examples:\n"
                "- 'when is the next payday?' → events_to_filter=['payday']\n"
                "- 'who is out next week?' or 'who is on PTO on Friday?' → events_to_filter=['pto']\n"
                "- 'what's on the company calendar this week?' → omit events_to_filter to get everything.\n\n"
                "Pass timezone-aware `start` and `end` datetimes bounding the window you "
                "want to search. Note: PTO events are typically all-day, so widen the "
                "window if you're checking a specific day.\n\n"
                "The calendar is cached between calls. Only set `force_refresh=True` "
                "if the user explicitly asks to refresh, reload, or bypass the cache "
                "(e.g. 'refresh the calendar', 'pull the latest calendar data', "
                "'the calendar looks stale, re-check'). Leave it False (the default) "
                "for every other request — even if the user is asking about 'today' "
                "or 'right now'."
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
                Tool(
                    _get_user_ids_in_user_group,
                    takes_ctx=False,
                    name="get_user_ids_in_user_group",
                    description=(
                        "Look up the Slack user IDs of the members of a Slack user group "
                        "(a.k.a. an @-mentionable group like @eng or @support-oncall).\n\n"
                        "The `group_name_or_id` argument accepts any of:\n"
                        "- A group ID (starts with 'S' followed by 10 uppercase alphanumerics, "
                        "e.g. 'S0123456789'). Slack often surfaces the raw ID inline in messages "
                        "that reference a group — pass it through directly if you already have it.\n"
                        "- A group handle (e.g. 'eng' or '@eng' — the leading @ is optional).\n"
                        "- A group display name (e.g. 'Engineering'). Matching is case-insensitive.\n\n"
                        "Returns a list of Slack user IDs, or an error string if the group could "
                        "not be found or the lookup failed.\n\n"
                        'Use when the user asks things like "who is in the @eng group?", '
                        '"list members of the on-call rotation", or "who is on the design team?". '
                        "The returned IDs can be passed to other tools (e.g. fetch_user_info) "
                        "to get names, emails, or other details for each member."
                    ),
                ),
                Tool(
                    _get_user_ids_in_channel,
                    takes_ctx=False,
                    name="get_user_ids_in_channel",
                    description=(
                        "List the Slack user IDs of every member of a Slack channel.\n\n"
                        "The `channel_id` argument must be a Slack channel ID: the letter 'C' "
                        "followed by 10 uppercase alphanumeric characters (e.g. 'C0123456789'). "
                        "Channel names ('#general', 'general') and links are NOT accepted — "
                        "if the user references a channel by name, resolve it to an ID first "
                        "using the appropriate lookup tool.\n\n"
                        "Returns a list of Slack user IDs, or an error string if the channel "
                        "could not be read (e.g. the bot is not a member, the ID is invalid, "
                        "or the channel is private and inaccessible). Results are automatically "
                        "paginated across large channels.\n\n"
                        'Use when the user asks things like "who is in this channel?", '
                        '"list the members of C0123456789", or "get everyone in the channel". '
                        "The returned IDs can be passed to other tools (e.g. fetch_user_info) "
                        "to get names, emails, or other details for each member."
                    ),
                ),
                *(
                    [
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
                        ),
                    ]
                    if LOGFIRE_READ_TOKEN
                    else []
                ),
            ]
            if isinstance(event, SlackBaseEvent)
            else []
        ),
    ]

    pydantic_agent = Agent(
        capabilities=[ContextManagerCapability(max_tokens=950_000)],
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
