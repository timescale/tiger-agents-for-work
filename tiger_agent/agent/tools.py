from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic_ai import BinaryContent, Tool

from tiger_agent.agent.constants import USER_DEFINED_EVENTS_ENABLED
from tiger_agent.db.utils import (
    delete_user_defined_rule,
    get_salesforce_account_id_for_channel,
    insert_user_defined_rule,
    list_user_defined_rules,
    user_is_admin,
)
from tiger_agent.events import EVENT_TYPE_OPTIONS, EVENT_TYPES_BY_NAME
from tiger_agent.logfire.constants import LOGFIRE_READ_TOKEN
from tiger_agent.logfire.utils import (
    find_errors,
    get_log_by_id,
    get_logs_for_trace,
    get_tool_calls_for_event,
    get_trace_ids_for_event,
)
from tiger_agent.org_calendar.utils import get_calender_events
from tiger_agent.salesforce.types import (
    ServiceRecord,
    UserDefinedRule,
)
from tiger_agent.salesforce.utils import (
    EXT_TO_MIME,
    download_content_version_url,
    get_services_for_account,
)
from tiger_agent.slack.types import ChannelInfo, SlackBaseEvent
from tiger_agent.slack.utils import (
    channel_is_external,
    download_slack_hosted_file,
    find_user_group,
    get_user_ids_in_channel,
    get_user_ids_in_user_group,
    send_new_salesforce_case_workflow_form,
)
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext


def create_tools(
    hctx: HarnessContext, task: Task, channel_info: ChannelInfo
) -> list[Tool]:
    event = task.event

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

    async def _list_user_defined_rules() -> list[UserDefinedRule]:
        assert isinstance(event, SlackBaseEvent)
        return await list_user_defined_rules(pool=hctx.pool, owner_slack_id=event.user)

    async def _delete_user_defined_rule(rule_id: int) -> bool:
        assert isinstance(event, SlackBaseEvent)
        return await delete_user_defined_rule(
            pool=hctx.pool, rule_id=rule_id, owner_slack_id=event.user
        )

    async def _attach_file(filename: str, content: str | bytes) -> None:
        thread_ts = event.thread_ts or event.ts
        if isinstance(content, str):
            res = await hctx.app.client.files_upload_v2(
                filename=filename,
                content=content,
                channel=event.channel,
                thread_ts=thread_ts,
            )

            print(str(res))
        if isinstance(content, bytes):
            res = await hctx.app.client.files_upload_v2(
                filename=filename,
                file=content,
                channel=event.channel,
                thread_ts=thread_ts,
            )

    async def _create_user_defined_rule(
        name: str,
        event_type: str,
        criteria: str,
        action_prompt: str,
        criteria_examples: list[str] | None = None,
    ) -> UserDefinedRule:
        assert isinstance(event, SlackBaseEvent)
        if event_type not in EVENT_TYPES_BY_NAME:
            raise ValueError(
                f"Unknown event_type {event_type!r}. "
                f"Valid options: {', '.join(EVENT_TYPES_BY_NAME)}"
            )
        cls = EVENT_TYPES_BY_NAME[event_type]
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

    async def _get_tool_calls_for_event(
        lookback_hours: float = 24.0,
    ) -> list[dict[str, Any]] | None:
        return await get_tool_calls_for_event(
            event=event, lookback_hours=lookback_hours
        )

    async def _get_logs_for_event(
        lookback_hours: float = 24.0,
        errors_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]] | None:
        assert isinstance(event, SlackBaseEvent)
        trace_ids = await get_trace_ids_for_event(
            event=event, lookback_hours=lookback_hours
        )
        if not trace_ids:
            return None
        results: list[dict[str, Any]] = []
        for trace_id in trace_ids:
            results.extend(
                await get_logs_for_trace(
                    trace_id=trace_id,
                    lookback_hours=lookback_hours,
                    errors_only=errors_only,
                    limit=limit,
                )
            )
        return results

    async def _get_log_by_id(
        span_id: str, lookback_hours: float = 24.0
    ) -> dict[str, Any] | None:
        return await get_log_by_id(span_id=span_id, lookback_hours=lookback_hours)

    async def _find_errors(
        lookback_hours: float = 24.0, limit: int = 100
    ) -> list[dict[str, Any]] | str:
        assert isinstance(event, SlackBaseEvent)
        if not await user_is_admin(pool=hctx.pool, user_id=event.user):
            return "This tool can only be used by admins."
        return await find_errors(lookback_hours=lookback_hours, limit=limit)

    async def _show_salesforce_case_form() -> str:
        assert isinstance(event, SlackBaseEvent)
        if not event.user:
            return "Cannot show case form: no user is associated with this event."

        account_id = await get_salesforce_account_id_for_channel(
            pool=hctx.pool, channel_id=event.channel
        )
        if not account_id:
            return (
                "This Slack channel is not linked to a Salesforce account, "
                "so I cannot open a support case from here. "
                "Please contact an admin to link this channel to a Salesforce account."
            )

        services: list[ServiceRecord] | None = None
        if hctx.salesforce_client:
            services = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: get_services_for_account(
                    salesforce_client=hctx.salesforce_client, account_id=account_id
                ),
            )

        try:
            await send_new_salesforce_case_workflow_form(
                client=hctx.app.client,
                channel=event.channel,
                user=event.user,
                services=services,
            )
        except Exception as e:
            return f"Sorry, I couldn't display the case creation form right now: {e}"

        return (
            "I've sent you an ephemeral form to open a new support case. "
            "Only you can see it — fill it out and submit when ready."
        )

    # if the handling of this event has a destination output in a customer-facing
    # channel, we do not want to expose all of the available tooling, but just a list of
    # tools that are deemed customer facing.
    if channel_is_external(channel_info=channel_info):
        return [
            Tool(
                _show_salesforce_case_form,
                takes_ctx=False,
                name="show_salesforce_case_form",
                description=(
                    "Show an ephemeral form in the current Slack channel for creating a new Salesforce support case. "
                    "This form is only visible to the requesting user. The form dynamically populates a service/project "
                    "dropdown based on the Salesforce account linked to this channel.\n\n"
                    "Use when the user asks to:\n"
                    "- open a support ticket/case\n"
                    "- create a new support case\n"
                    "- report an issue that needs a ticket\n"
                    "- file a bug or problem report\n"
                    "- get help with a technical issue that should be tracked in Salesforce\n\n"
                    "If this channel is not linked to a Salesforce account, the tool returns an explanatory message."
                ),
            ),
        ]

    return [
        Tool(
            download_slack_hosted_file,
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
                                f"event_type must be one of:\n{EVENT_TYPE_OPTIONS}\n"
                                "criteria_examples are optional but improve matching accuracy."
                            ),
                        ),
                    ]
                    if USER_DEFINED_EVENTS_ENABLED
                    else []
                ),
                Tool(
                    _attach_file,
                    takes_ctx=False,
                    name="attach_file_to_slack_thread",
                    description="Attach a snippet or attachment to the current thread. If the content type is a string, will be attached as a snippet, if the content type is a byte array, will be attached as a file.",
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
                        Tool(
                            _get_logs_for_event,
                            takes_ctx=False,
                            name="get_logs_for_event",
                            description=(
                                "Retrieve a condensed list of Logfire records (spans + logs) "
                                "produced while handling the current Slack event. Each row "
                                "contains start_timestamp, span_id, span_name, level, message, "
                                "is_exception, and otel_status_message. Pass a span_id from a "
                                "returned row to `get_log_by_id` to fetch the full record.\n\n"
                                "Set `errors_only=True` to restrict results to exceptions / "
                                "error-level records. "
                                'Use when the user asks things like "what happened?", '
                                '"show me the logs for this thread", or "were there any errors?".'
                            ),
                        ),
                        Tool(
                            _get_log_by_id,
                            takes_ctx=False,
                            name="get_log_by_id",
                            description=(
                                "Fetch the full Logfire record for a given `span_id` — including "
                                "attributes, otel_events, and otel_links. Use after "
                                "`get_logs_for_event` to drill into a specific row."
                            ),
                        ),
                        Tool(
                            _find_errors,
                            takes_ctx=False,
                            name="find_errors",
                            description=(
                                "Find error and exception records across all traces within the "
                                "given `lookback_hours` window (default 24). Returns rows with "
                                "start_timestamp, trace_id, span_id, span_name, and message, "
                                "ordered newest-first. Pass a returned `span_id` to "
                                "`get_log_by_id` or a `trace_id` to fetch related logs. "
                                'Use when the user asks things like "any errors in the last hour?", '
                                '"what\'s been failing today?", or "show me recent exceptions".'
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
