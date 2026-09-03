from typing import cast

import logfire

from tiger_agent.agent.types import AgentSalesforceResponse
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import upsert_feedback_request_reminder
from tiger_agent.salesforce.constants import (
    SALESFORCE_CASE_CHANNEL,
    SALESFORCE_SLACK_THREAD_FIELD,
)
from tiger_agent.salesforce.types import SalesforceAssignmentChangedEvent
from tiger_agent.salesforce.utils import create_case_url, update_case
from tiger_agent.slack.types import FeedbackReminderThread, SlackMessage
from tiger_agent.slack.utils import (
    fetch_end_of_day_for_user,
    get_handle_link,
    post_response,
    request_feedback,
)
from tiger_agent.tasks.handlers.base import AGENT_USAGE_LIMITS, TaskHandler
from tiger_agent.tasks.types import Task


class SalesforceAssignmentChangedHandler(TaskHandler):
    """
    Runs the agent to produce a case summary and posts it to the Salesforce
    case channel. Updates the Salesforce case with the Slack thread permalink.
    """

    EVENT_TYPES = [SalesforceAssignmentChangedEvent]

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
            usage_limits=AGENT_USAGE_LIMITS,
        )
        output = cast(AgentSalesforceResponse, response.output)

        case_owner_user_id = output.case_owner_slack_user_id

        original_message = await post_response(
            client=hctx.app.client,
            channel=SALESFORCE_CASE_CHANNEL,
            thread_ts=None,
            text=f"*New Case* <{create_case_url(event.case.Id)}|{event.case.CaseNumber}> - _{event.case.Subject}_{f', assigned to {get_handle_link(case_owner_user_id)}' if case_owner_user_id else ''}:thread: \n```\n{output.short_description}\n```",
        )

        if not original_message:
            logfire.error("Failed to post message, aborting")
            return

        message_to_link_to = SlackMessage(
            channel_id=SALESFORCE_CASE_CHANNEL,
            ts=original_message.data.get("ts"),
            text=output.message,
            thread_ts=None,
            to_user_id=case_owner_user_id,
        )

        await post_response(
            client=hctx.app.client,
            channel=SALESFORCE_CASE_CHANNEL,
            thread_ts=message_to_link_to.ts,
            text=output.message,
        )

        if message_to_link_to and SALESFORCE_SLACK_THREAD_FIELD:
            if event.update_link_to_thread:
                result = await hctx.app.client.chat_getPermalink(
                    channel=message_to_link_to.channel_id,
                    message_ts=message_to_link_to.ts,
                )
                permalink = result.data.get("permalink")
                update_case(
                    hctx.salesforce_client,
                    event.case.Id,
                    {SALESFORCE_SLACK_THREAD_FIELD: permalink},
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
