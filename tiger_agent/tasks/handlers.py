"""Task handlers for Tiger Agent.

TaskHandler and TaskProcessor define the dispatch interface. Each concrete
handler receives hctx and agent via constructor injection and only needs
the task in its handle() method.
"""

import asyncio
import logging
from abc import ABC, abstractmethod

import logfire
from htmlslacker import HTMLSlacker
from pydantic_ai import UsageLimits

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import (
    add_salesforce_case_thread,
    get_salesforce_account_id_for_channel,
    get_salesforce_case_thread_thread_id,
    usage_limit_reached,
    user_ignored,
)
from tiger_agent.salesforce.constants import (
    SALESFORCE_CASE_CHANNEL,
    SALESFORCE_ENABLE_SPAM_FILTERING,
    SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD,
    SALESFORCE_SLACK_THREAD_FIELD,
)
from tiger_agent.salesforce.types import (
    SalesforceAssignmentChangedEvent,
    SalesforceBaseEvent,
    SalesforceCreateNewCaseEvent,
    SalesforceFeedItemEvent,
)
from tiger_agent.salesforce.utils import (
    create_case,
    create_case_url,
    download_feed_attachment,
    get_feed_attachment_ids,
)
from tiger_agent.slack.types import (
    SlackAppMentionEvent,
    SlackMessage,
    SlackMessageEvent,
)
from tiger_agent.slack.utils import (
    add_reaction,
    post_response,
    send_feedback_rating_prompt,
    set_status,
    stream_response_to_mention,
)
from tiger_agent.tasks.types import Task
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
            if not isinstance(event, SalesforceBaseEvent):
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


# backwards-compat alias
EventProcessor = TaskProcessor


class SlackTaskHandler(TaskHandler):
    """Handles SlackAppMentionEvent and SlackMessageEvent via LLM."""

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

        async for stream_event in agent_and_ctx.agent.run_stream_events(
            user_prompt=agent_and_ctx.user_prompt,
            deps=agent_and_ctx.ctx,
            usage_limits=UsageLimits(output_tokens_limit=9_000),
        ):
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
    """Handles SalesforceAssignmentChangedEvent

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

        original_message = await post_response(
            client=hctx.app.client,
            channel=SALESFORCE_CASE_CHANNEL,
            thread_ts=None,
            text=f"*New Case* <{create_case_url(event.case)}|{event.case.CaseNumber}> - _{event.case.Subject}_{f', assigned to <@{response.output.case_owner_slack_user_id}>' if response.output.case_owner_slack_user_id else ''}:thread: \n```\n{response.output.short_description_of_case}\n```",
        )

        message_to_link_to = SlackMessage(
            channel_id=SALESFORCE_CASE_CHANNEL,
            ts=original_message.data.get("ts"),
            text=response.output.message,
            thread_ts=None,
            to_user_id=response.output.case_owner_slack_user_id,
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

            if message_to_link_to.to_user_id:

                async def _delayed_feedback(client, message):
                    await asyncio.sleep(10)
                    await send_feedback_rating_prompt(client, message)

                asyncio.create_task(
                    _delayed_feedback(hctx.app.client, message_to_link_to)
                )


class SalesforceCreateCaseHandler(TaskHandler):
    """Handles SalesforceCreateNewCaseEvent

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
            text=f"*Support Case Created*\nCase Number: {new_case.CaseNumber}\nSubject: {new_case.Subject} \nDescription: {new_case.Description}",
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
    """Handles SalesforceFeedItemEvent — no LLM required.

    Syncs a Salesforce Chatter post on a case to the linked Slack thread.
    """

    @logfire.instrument("SalesforceFeedItemHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SalesforceFeedItemEvent = task.event
        [channel_id, thread_ts] = await get_salesforce_case_thread_thread_id(
            hctx.pool, case_id=event.feed_item.ParentId
        )

        markdown_conversion = HTMLSlacker(event.feed_item.Body).get_output().strip()
        body = "\n".join(f"> {line}" for line in markdown_conversion.splitlines())
        text = f"_From_ *{event.feed_item.CreatedBy.Name}* _via Tigerdata Support_\n\n{body}"

        attachment_ids = get_feed_attachment_ids(
            hctx.salesforce_client, event.feed_item.Id
        )
        image_attachments = [
            download_feed_attachment(hctx.salesforce_client, aid)
            for aid in attachment_ids
        ]

        await post_response(
            client=hctx.app.client,
            channel=channel_id,
            thread_ts=thread_ts,
            text=text,
            use_mrkdwn=True,
            image_attachments=image_attachments,
        )
