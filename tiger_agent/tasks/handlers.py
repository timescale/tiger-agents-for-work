"""Task handlers for Tiger Agent.

TaskHandler and TaskProcessor define the dispatch interface. Each concrete
handler receives hctx and agent via constructor injection and only needs
the task in its handle() method.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod

import logfire
from htmlslacker import HTMLSlacker
from pydantic_ai import Agent, Tool, UsageLimits

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import (
    add_salesforce_case_thread,
    get_salesforce_account_id_for_channel,
    get_salesforce_case_thread_thread_id,
    upsert_feedback_request_reminder,
    usage_limit_reached,
    user_ignored,
)
from tiger_agent.salesforce.constants import (
    SALESFORCE_CASE_CHANNEL,
    SALESFORCE_CASE_EMAIL_COMMENT_SUBJECT,
    SALESFORCE_CASE_SUPPORT_EMAIL,
    SALESFORCE_ENABLE_SPAM_FILTERING,
    SALESFORCE_INTERNAL_FROM_NAME_SUFFIX,
    SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD,
    SALESFORCE_SLACK_THREAD_FIELD,
)
from tiger_agent.salesforce.types import (
    SalesforceAssignmentChangedEvent,
    SalesforceBaseEvent,
    SalesforceCaseCreatedEvent,
    SalesforceCaseStatusChangedEvent,
    SalesforceCreateNewCaseEvent,
    SalesforceFeedItemEvent,
    UserDefinedRuleMatch,
)
from tiger_agent.salesforce.utils import (
    add_case_email_comment,
    add_internal_case_post,
    build_email_attachments_from_slack_files,
    create_case,
    create_case_url,
    download_feed_attachment,
    get_feed_attachment_ids,
    replace_all_slack_mentions_with_links_to_profile,
    slack_safe_subject,
)
from tiger_agent.slack.constants import AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL
from tiger_agent.slack.types import (
    AgentFeedbackRatingEvent,
    AgentFeedbackRatingSubtype,
    AgentFeedbackRequestReminderEvent,
    FeedbackReminderThread,
    SlackAppMentionEvent,
    SlackMessage,
    SlackMessageEvent,
    SlackSalesforceCaseThreadMessageEvent,
)
from tiger_agent.slack.utils import (
    add_quote_block,
    add_reaction,
    fetch_end_of_day_for_user,
    fetch_user_info,
    get_a_href_link_to_user_profile,
    get_channel_link,
    get_handle_link,
    post_response,
    request_feedback,
    set_status,
    stream_response_to_mention,
    user_is_external,
)
from tiger_agent.tasks.types import Task
from tiger_agent.tasks.user_defined_rules import evaluate_user_defined_rules
from tiger_agent.types import HarnessContext

logger = logging.getLogger(__name__)


class TaskHandler(ABC):
    """Abstract base class for event handlers registered with TaskProcessor."""

    def __init__(self, hctx: HarnessContext) -> None:
        self._hctx = hctx

    @abstractmethod
    async def handle(self, task: Task) -> None: ...


class TaskProcessor:
    """Routes tasks to registered handlers by event type.

    Register a TaskHandler instance for each event type. When a task is
    processed, the processor dispatches to the matching handler and wraps
    the call with error handling and Slack feedback for non-Salesforce events.
    """

    def __init__(self, hctx: HarnessContext, agent: TigerAgent) -> None:
        self._hctx = hctx
        self._agent = agent
        self._handlers: dict[type, TaskHandler] = {}

    def register(self, event_types: type | list[type], handler: TaskHandler) -> None:
        if isinstance(event_types, list):
            for event_type in event_types:
                self._handlers[event_type] = handler
        else:
            self._handlers[event_types] = handler

    async def __call__(self, hctx: HarnessContext, task: Task) -> None:
        event = task.event
        handler = self._handlers.get(type(event))
        if handler is None:
            logfire.warn(
                "No handler registered for event type",
                event_type=type(event).__name__,
            )
            return
        try:
            await handler.handle(task)
        except Exception as e:
            logger.exception("handler failed", exc_info=e)
            if not isinstance(event, (SalesforceBaseEvent, UserDefinedRuleMatch)):
                await add_reaction(hctx.app.client, event.channel, event.ts, "x")
                await post_response(
                    client=hctx.app.client,
                    channel=event.channel,
                    thread_ts=event.thread_ts if event.thread_ts else event.ts,
                    text="I experienced an issue trying to respond. I will try again."
                    if task.attempts < self._agent.max_attempts
                    else "I give up. Sorry.",
                )
            raise

        # skip rule evaluation for match events themselves to avoid loops
        if not isinstance(event, UserDefinedRuleMatch):
            await evaluate_user_defined_rules(
                pool=hctx.pool,
                event_type=event.type,
                event_dict=task.event.model_dump(),
            )


class SlackTaskHandler(TaskHandler):
    def __init__(self, hctx: HarnessContext, agent: TigerAgent) -> None:
        super().__init__(hctx)
        self._agent = agent

    @logfire.instrument("SlackTaskHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SlackAppMentionEvent | SlackMessageEvent = task.event

        if await user_ignored(pool=hctx.pool, user_id=event.user):
            logfire.info("Ignore user", user_id=event.user)
            return

        if await usage_limit_reached(
            pool=hctx.pool,
            user_id=event.user,
            interval=self._agent.rate_limit_interval,
            allowed_requests=self._agent.rate_limit_allowed_requests,
        ):
            logfire.info(
                "User interaction limited due to usage",
                allowed_requests=self._agent.rate_limit_allowed_requests,
                interval=self._agent.rate_limit_interval,
                user_id=event.user,
            )
            await post_response(
                client=hctx.app.client,
                channel=event.channel,
                thread_ts=event.thread_ts or event.ts,
                text="I cannot process your request at this time due to usage limits. Please ask me again later.",
            )
            return

        agent_and_ctx = await create_agent_and_context(
            hctx=hctx,
            task=task,
            agent=self._agent,
            channel_to_respond=event.channel,
        )

        await set_status(
            client=hctx.app.client,
            channel_id=event.channel,
            thread_ts=event.thread_ts or event.ts,
            is_busy=True,
        )
        slack_stream = None

        async with agent_and_ctx.agent.run_stream_events(
            user_prompt=agent_and_ctx.user_prompt,
            deps=agent_and_ctx.ctx,
            usage_limits=UsageLimits(output_tokens_limit=9_000),
        ) as stream_events:
            async for stream_event in stream_events:
                slack_stream = await stream_response_to_mention(
                    client=hctx.app.client,
                    slack_stream=slack_stream,
                    stream_event=stream_event,
                    channel_id=event.channel,
                    recipient_user_id=event.user,
                    recipient_team_id=hctx.bot_info.team_id,
                    ts=event.ts,
                    thread_ts=event.thread_ts,
                )

        if slack_stream is not None and slack_stream._state != "completed":
            rest = await slack_stream.stop()
            logfire.info("ended", extra={"res": rest})

        await set_status(
            client=hctx.app.client,
            channel_id=event.channel,
            thread_ts=event.thread_ts or event.ts,
            is_busy=False,
        )
        await add_reaction(hctx.app.client, event.channel, event.ts, "white_check_mark")


class SalesforceAssignmentChangedHandler(TaskHandler):
    """
    Runs the agent to produce a case summary and posts it to the Salesforce
    case channel. Updates the Salesforce case with the Slack thread permalink.
    """

    def __init__(self, hctx: HarnessContext, agent: TigerAgent) -> None:
        super().__init__(hctx)
        self._agent = agent

    @logfire.instrument("SalesforceAssignmentChangedHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SalesforceAssignmentChangedEvent = task.event

        agent_and_ctx = await create_agent_and_context(
            hctx=hctx,
            task=task,
            agent=self._agent,
            channel_to_respond=SALESFORCE_CASE_CHANNEL,
        )

        response = await agent_and_ctx.agent.run(
            user_prompt=agent_and_ctx.user_prompt,
            deps=agent_and_ctx.ctx,
            usage_limits=UsageLimits(output_tokens_limit=9_000),
        )

        if response.output.is_spam:
            logfire.info(
                "Salesforce case identified as spam",
                extra={"filtering_enabled": SALESFORCE_ENABLE_SPAM_FILTERING},
            )
            if SALESFORCE_ENABLE_SPAM_FILTERING:
                return

        case_owner_user_id = response.output.case_owner_slack_user_id

        original_message = await post_response(
            client=hctx.app.client,
            channel=SALESFORCE_CASE_CHANNEL,
            thread_ts=None,
            use_mrkdwn=True,
            text=f"*New Case* <{create_case_url(event.case.Id)}|{event.case.CaseNumber}> - _{slack_safe_subject(event.case.Subject)}_{f', assigned to {get_handle_link(case_owner_user_id)}' if case_owner_user_id else ''}:thread: \n```\n{response.output.short_description_of_case}\n```",
        )

        message_to_link_to = SlackMessage(
            channel_id=SALESFORCE_CASE_CHANNEL,
            ts=original_message.data.get("ts"),
            text=response.output.message,
            thread_ts=None,
            to_user_id=case_owner_user_id,
        )

        await post_response(
            client=hctx.app.client,
            channel=SALESFORCE_CASE_CHANNEL,
            thread_ts=message_to_link_to.ts,
            text=response.output.message,
        )

        if message_to_link_to and SALESFORCE_SLACK_THREAD_FIELD:
            if event.update_link_to_thread:
                result = await hctx.app.client.chat_getPermalink(
                    channel=message_to_link_to.channel_id,
                    message_ts=message_to_link_to.ts,
                )
                permalink = result.data.get("permalink")
                hctx.salesforce_client.Case.update(
                    event.case.Id,
                    {SALESFORCE_SLACK_THREAD_FIELD: permalink},
                    headers={"Sforce-Auto-Assign": "false"},
                )
                logfire.info(
                    "Updated Salesforce case to include the thread link",
                    extra={"permalink": permalink},
                )

            if case_owner_user_id:
                users_end_of_day = await fetch_end_of_day_for_user(
                    client=hctx.app.client, user_id=case_owner_user_id
                )

                await upsert_feedback_request_reminder(
                    pool=hctx.pool,
                    user_id=case_owner_user_id,
                    thread=FeedbackReminderThread(
                        channel=message_to_link_to.channel_id,
                        message_ts=message_to_link_to.ts,
                        label=event.case.CaseNumber,
                    ),
                    action="add",
                    reminder_datetime=users_end_of_day,
                )

            request_feedback(
                hctx.app.client,
                channel=message_to_link_to.channel_id,
                thread_ts=message_to_link_to.ts,
            )


class SalesforceCaseCreatedHandler(TaskHandler):
    """
    Runs the agent to determine if the case is spam.
    We handle legitimate new cases with the SalesforceAssignmentChangedHandler
    as, at that point we have a assignee and spam should have been filtered out
    So this handler is strictly to detect spam cases
    """

    def __init__(self, hctx: HarnessContext, agent: TigerAgent) -> None:
        super().__init__(hctx)
        self._agent = agent

    @logfire.instrument("SalesforceCaseCreatedHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SalesforceCaseCreatedEvent = task.event

        if not SALESFORCE_ENABLE_SPAM_FILTERING:
            return

        agent_and_ctx = await create_agent_and_context(
            hctx=hctx,
            task=task,
            agent=self._agent,
            channel_to_respond=SALESFORCE_CASE_CHANNEL,
        )

        response = await agent_and_ctx.agent.run(
            user_prompt=agent_and_ctx.user_prompt,
            deps=agent_and_ctx.ctx,
            usage_limits=UsageLimits(output_tokens_limit=9_000),
        )

        if not response.output.is_spam:
            return

        logfire.info(
            "Salesforce case identified as spam",
            extra={"filtering_enabled": SALESFORCE_ENABLE_SPAM_FILTERING},
        )

        original_message = await post_response(
            client=hctx.app.client,
            channel=SALESFORCE_CASE_CHANNEL,
            thread_ts=None,
            use_mrkdwn=True,
            text=f"*Spam Detected* <{create_case_url(event.case.Id)}|{event.case.CaseNumber}> - _{slack_safe_subject(event.case.Subject)}_",
        )

        message_to_link_to = SlackMessage(
            channel_id=SALESFORCE_CASE_CHANNEL,
            ts=original_message.data.get("ts"),
            text=response.output.message,
            thread_ts=None,
        )

        if message_to_link_to and SALESFORCE_SLACK_THREAD_FIELD:
            result = await hctx.app.client.chat_getPermalink(
                channel=message_to_link_to.channel_id,
                message_ts=message_to_link_to.ts,
            )
            permalink = result.data.get("permalink")
            hctx.salesforce_client.Case.update(
                event.case.Id,
                {SALESFORCE_SLACK_THREAD_FIELD: permalink},
                headers={"Sforce-Auto-Assign": "false"},
            )
            logfire.info(
                "Updated Salesforce case to include the thread link",
                extra={"permalink": permalink},
            )

        add_internal_case_post(
            salesforce_client=hctx.salesforce_client,
            case_id=event.case.Id,
            body=response.output.short_description_of_case,
        )
        request_feedback(
            hctx.app.client,
            channel=message_to_link_to.channel_id,
            thread_ts=message_to_link_to.ts,
        )


class AgentFeedbackRequestReminderHandler(TaskHandler):
    """
    When the agent supplies feedback, we have a mechanism that will remind the recipient
    to leave feedback. At this time, the only scenario that this will happen is when the agent
    gives a suggested response for a new Salesforce case. When the new case event occurs,
    we enqueue a AgentFeedbackRequestReminderEvent with a future vt -- this effectively
    schedules the reminder for the future (e.g. at the end of the support engineer's day)
    """

    @logfire.instrument(
        "AgentFeedbackRequestReminderHandler.handle", extract_args=False
    )
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: AgentFeedbackRequestReminderEvent = task.event

        permalink_results = await asyncio.gather(
            *[
                hctx.app.client.chat_getPermalink(
                    channel=t.channel, message_ts=t.message_ts
                )
                for t in event.threads
            ]
        )
        thread_links = "\n".join(
            f"• <{result.data.get('permalink')}|{t.label}>"
            for t, result in zip(event.threads, permalink_results, strict=True)
        )
        await post_response(
            client=hctx.app.client,
            channel=event.user,
            thread_ts=None,
            text=f"Hey! Thanks for all your support today. When you get a chance, we'd love to hear your thoughts on these conversations:\n{thread_links}",
        )


class SalesforceCreateCaseHandler(TaskHandler):
    """
    Creates a Salesforce case from a Slack-initiated form submission and posts
    a confirmation message to the originating channel.
    """

    @logfire.instrument("SalesforceCreateCaseHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SalesforceCreateNewCaseEvent = task.event
        channel_to_respond = event.channel
        account_id_for_channel = await get_salesforce_account_id_for_channel(
            pool=hctx.pool, channel_id=channel_to_respond
        )

        if not account_id_for_channel:
            logfire.warn(
                "Skipping Salesforce case creation. No Salesforce account associated with the channel.",
                channel=channel_to_respond,
                user=event.user,
            )
            return

        new_case = create_case(
            salesforce_client=hctx.salesforce_client,
            subject=event.subject,
            description=event.description,
            severity=event.severity,
            account_id=account_id_for_channel,
            project_id=event.project_id,
            service_id=event.service_id,
            origin="Slack",
        )
        response = await post_response(
            client=hctx.app.client,
            channel=channel_to_respond,
            thread_ts=None,
            text="\n".join(
                [
                    "*Support Case Created*",
                    f"_Submitter:_ {get_handle_link(event.user)}",
                    f"_Case Number:_ `{new_case.CaseNumber}`",
                    f"_Subject:_ `{new_case.Subject}`",
                    *(
                        [f"_Project Id:_: `{new_case.Cloud_Project_ID__c}`"]
                        if new_case.Cloud_Project_ID__c
                        else []
                    ),
                    *(
                        [f"_Service Id:_: `{new_case.Cloud_Service_ID__c}`"]
                        if new_case.Cloud_Service_ID__c
                        else []
                    ),
                    "_Description:_",
                    add_quote_block(new_case.Description),
                ]
            ),
            use_mrkdwn=True,
        )

        new_case_thread_ts = response.data.get("ts", None)
        if not new_case_thread_ts:
            raise Exception(
                "Could not create a thread for the customer-created Salesforce case"
            )

        await add_salesforce_case_thread(
            hctx.pool,
            thread_ts=new_case_thread_ts,
            channel_id=channel_to_respond,
            case_id=new_case.Id,
        )

        if not SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD:
            logfire.error(
                "SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD not specified, skipping"
            )
            return

        result = await hctx.app.client.chat_getPermalink(
            channel=channel_to_respond,
            message_ts=new_case_thread_ts,
        )
        permalink = result.data.get("permalink")
        hctx.salesforce_client.Case.update(
            new_case.Id,
            {SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD: permalink},
            headers={"Sforce-Auto-Assign": "false"},
        )
        logfire.info(
            "Updated Salesforce case to include the customer thread link",
            extra={"permalink": permalink},
        )


class SalesforceFeedItemHandler(TaskHandler):
    """
    Syncs a Salesforce post on a case to the linked Slack thread.
    """

    @logfire.instrument("SalesforceFeedItemHandler.handle", extract_args=["task"])
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SalesforceFeedItemEvent = task.event
        result = await get_salesforce_case_thread_thread_id(
            hctx.pool, case_id=event.feed_item.ParentId
        )

        if not result:
            # if the FeedItem's case is not associated with a Slack thread, do nothing
            return

        [channel_id, thread_ts] = result

        markdown_conversion = HTMLSlacker(event.feed_item.Body).get_output().strip()
        body = add_quote_block(markdown_conversion)
        text = f"_From_ *{event.feed_item.CreatedBy.Name}* _via Tigerdata Support_\n\n{body}"

        attachment_ids = get_feed_attachment_ids(
            hctx.salesforce_client, event.feed_item.Id
        )
        file_attachments = [
            a
            for aid in attachment_ids
            if (a := download_feed_attachment(hctx.salesforce_client, aid)) is not None
        ]

        await post_response(
            client=hctx.app.client,
            channel=channel_id,
            thread_ts=thread_ts,
            text=text,
            use_mrkdwn=True,
            file_attachments=file_attachments,
        )


class SlackSalesforceCaseThreadMessageHandler(TaskHandler):
    """
    Syncs a Slack message posted in a Salesforce-linked thread back to the
    Salesforce case as an email comment, including any file attachments.
    """

    @logfire.instrument(
        "SlackSalesforceCaseThreadMessageHandler.handle", extract_args=False
    )
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SlackSalesforceCaseThreadMessageEvent = task.event

        user_info = await fetch_user_info(hctx.app.client, user_id=event.user)
        is_external_user = user_is_external(bot_info=hctx.bot_info, user_info=user_info)
        link_to_user_profile = await get_a_href_link_to_user_profile(
            hctx=hctx, user_info=user_info
        )

        text_prefix = f"[Replied via Slack as @{user_info.name}]"
        html_prefix = f"[Replied via Slack as {link_to_user_profile}</a>]"

        attachments = await build_email_attachments_from_slack_files(
            client=hctx.app.client, event=event
        )

        [
            html_message_body,
            plain_message_body,
        ] = await replace_all_slack_mentions_with_links_to_profile(
            hctx=hctx, message=event.text
        )

        add_case_email_comment(
            hctx.salesforce_client,
            case_id=event.salesforce_case_id,
            body=f"{text_prefix}\n{plain_message_body}",
            html_body=f"<p>{html_prefix}</p><p>{html_message_body}</p>",
            from_address=user_info.profile.email,
            to_address=SALESFORCE_CASE_SUPPORT_EMAIL if is_external_user else None,
            subject=SALESFORCE_CASE_EMAIL_COMMENT_SUBJECT,
            from_name=f"{user_info.real_name} ({SALESFORCE_INTERNAL_FROM_NAME_SUFFIX})"
            if not is_external_user
            else None,
            attachments=attachments if attachments else None,
        )

        logfire.info(
            "Synced Slack message to Salesforce",
            case_id=event.salesforce_case_id,
            comment_body=event.text,
            user_id=event.user,
            user_name=user_info.real_name,
            user_is_external=is_external_user,
        )


class SalesforceCaseStatusChangedHandler(TaskHandler):
    """
    Called when a Salesforce case status changes.
    """

    @logfire.instrument("SalesforceCaseStatusChangedHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SalesforceCaseStatusChangedEvent = task.event

        await post_response(
            client=hctx.app.client,
            channel=event.slack_channel_id,
            thread_ts=event.slack_thread_ts,
            text=f"_Case status updated to_ `{event.case.Status}`",
        )


class AgentFeedbackRatingHandler(TaskHandler):
    """
    Called when a Salesforce case status changes.
    """

    @logfire.instrument("AgentFeedbackRatingHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: AgentFeedbackRatingEvent = task.event

        if not AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL:
            logfire.info(
                "AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL not specified, not posting results."
            )
            return

        if event.subtype == AgentFeedbackRatingSubtype.external:
            await post_response(
                client=hctx.app.client,
                channel=AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL,
                thread_ts=None,
                use_mrkdwn=True,
                text="\n".join(
                    [
                        "*Feedback Received*",
                        *([f"_Source:_ `{event.subtype}`"] if event.subtype else []),
                        *(
                            [f"_Rating:_ `{event.rating}/5`"]
                            if event.rating is not None
                            else []
                        ),
                        *(
                            [f"_User:_ {get_handle_link(event.user)}"]
                            if event.user
                            else []
                        ),
                        *(
                            [f"_Channel:_ {get_channel_link(event.channel)}"]
                            if event.channel
                            else []
                        ),
                        *(
                            [
                                f"_Description:_ \n{'\n'.join(f'> {line}' for line in event.description.splitlines())}"
                            ]
                            if event.description
                            else []
                        ),
                    ]
                ),
            )


class UserDefinedRuleMatchHandler(TaskHandler):
    def __init__(self, hctx: HarnessContext) -> None:
        super().__init__(hctx)

    @logfire.instrument("UserDefinedRuleMatchHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: UserDefinedRuleMatch = task.event

        async def _send_dm(user_id: str, message: str) -> None:
            await post_response(
                client=hctx.app.client,
                channel=user_id,
                thread_ts=None,
                text=message,
            )

        async def _send_channel_message(channel_id: str, message: str) -> None:
            await post_response(
                client=hctx.app.client,
                channel=channel_id,
                thread_ts=None,
                text=message,
            )

        def _get_case_url(case_id: str) -> str:
            """Return the Salesforce URL for a case. The case_id can often be obtained
            from a Salesforce object's ParentId field (e.g. on FeedItem, Task, etc.)."""
            return create_case_url(case_id)

        agent = Agent(
            model="anthropic:claude-opus-4-7",
            system_prompt=(
                "You are an automated action agent. A custom monitoring rule has matched an incoming "
                "event and you must carry out the action described in the user prompt. "
                "Use the send_dm tool to notify users or send_channel_message to post to a channel. "
                "Act immediately — do not ask clarifying questions "
                "and do not add conversational framing."
            ),
            tools=[
                Tool(_send_dm, takes_ctx=False, name="send_dm"),
                Tool(
                    _send_channel_message, takes_ctx=False, name="send_channel_message"
                ),
                Tool(_get_case_url, takes_ctx=False, name="get_case_url"),
            ],
        )

        user_prompt = (
            f"{event.action_prompt}\n\n"
            f"## Event Payload\n\n"
            f"```\n{json.dumps(event.matched_event, indent=2, default=str)}\n```"
        )

        await agent.run(
            user_prompt=user_prompt,
            usage_limits=UsageLimits(output_tokens_limit=9_000),
        )
