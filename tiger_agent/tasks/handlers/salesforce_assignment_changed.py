from typing import cast

import logfire
from slack_sdk.web.async_client import AsyncWebClient

from tiger_agent.agent.limits import AGENT_USAGE_LIMITS, run_and_return_partial
from tiger_agent.agent.types import AgentSalesforceResponse
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import upsert_feedback_request_reminder
from tiger_agent.salesforce.constants import SALESFORCE_SLACK_THREAD_FIELD
from tiger_agent.salesforce.types import (
    CaseData,
    CaseFacts,
    SalesforceAssignmentChangedEvent,
)
from tiger_agent.salesforce.utils import create_case_url, get_case_facts, update_case
from tiger_agent.slack.types import FeedbackReminderThread, SlackMessage
from tiger_agent.slack.utils import (
    fetch_end_of_day_for_user,
    fetch_user_id_by_email,
    get_handle_link,
    post_response,
    request_feedback,
)
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.handlers.constants import SALESFORCE_QUEUE_ID_PREFIX
from tiger_agent.tasks.types import Task


async def resolve_case_owner_slack_user_id(
    client: AsyncWebClient, case: CaseData
) -> str | None:
    """Find the Slack user to mention for the case owner, or None.

    The owner is a Salesforce User (id prefix `005`) or a Queue (prefix `00G`).
    A queue has no Slack user, so it resolves to None without a lookup. A user
    resolves through one `users.lookupByEmail` call on the owner's Salesforce
    *username*, which is the login address on the current company domain;
    the Email field still carries pre-rename addresses for some staff, so it
    is not used. A username that is not an address, or one Slack does not
    know, resolves to None.
    """
    owner = case.Owner
    if owner is None or not owner.Username:
        return None
    if owner.Id and owner.Id.startswith(SALESFORCE_QUEUE_ID_PREFIX):
        return None
    username = owner.Username.strip().lower()
    if "@" not in username:
        return None
    return await fetch_user_id_by_email(client, username)


def case_details_lines(case: CaseData, facts: CaseFacts | None) -> list[str]:
    """The Case Details block of the notification, one mrkdwn line per fact.

    Rendered from the event and the facts query, so the agent does not have to
    copy identifiers into its reply. Fields that are unset are left out.
    """
    owner = case.Owner
    owner_name = (
        " ".join(part for part in (owner.FirstName, owner.LastName) if part)
        if owner is not None
        else ""
    )
    region = None
    if facts is not None and facts.cloud_region:
        region = facts.cloud_region
        if facts.is_production is not None:
            region += " (prod)" if facts.is_production else " (non-prod)"
    candidates: list[tuple[str, str | None]] = [
        ("Severity", case.Severity__c),
        ("Customer Impact", case.Cloud_Impact__c),
        ("Platform", facts.platform if facts else None),
        ("Account", facts.account_name if facts else None),
        ("Tier", facts.account_tier if facts else None),
        ("Billing Category", facts.billing_category if facts else None),
        ("Service ID", case.Cloud_Service_ID__c),
        ("Project ID", case.Cloud_Project_ID__c),
        ("Region", region),
        ("Case Owner", owner_name or None),
        ("Created", case.CreatedDate),
        (
            "Customer Thread",
            f"<{case.Customer_Slack_Thread__c}|link>"
            if case.Customer_Slack_Thread__c
            else None,
        ),
    ]
    return [f"*{label}:* {value}" for label, value in candidates if value]


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
        channel = event.destination_channel
        if channel is None:
            raise RuntimeError(
                "SalesforceAssignmentChangedEvent has no destination_channel; "
                "the listener sets it when the event is enqueued"
            )

        facts = get_case_facts(hctx.salesforce_client, event.case.Id)
        extra_context = {"case_facts": facts} if facts is not None else None
        agent_and_ctx = await create_agent_and_context(
            hctx=hctx, task=task, agent=self._agent, extra_context=extra_context
        )

        # A case summary built from partial research still helps the assignee;
        # dying on the budget leaves the case with no Slack thread at all.
        response = await run_and_return_partial(
            agent_and_ctx.agent,
            user_prompt=agent_and_ctx.user_prompt,
            deps=agent_and_ctx.ctx,
            usage_limits=AGENT_USAGE_LIMITS,
        )
        output = cast(AgentSalesforceResponse, response.output)

        case_owner_user_id = await resolve_case_owner_slack_user_id(
            hctx.app.client, event.case
        )

        assigned = (
            f", assigned to {get_handle_link(case_owner_user_id)}"
            if case_owner_user_id
            else ""
        )
        header = "\n".join(
            [
                f"*New Case* <{create_case_url(event.case.Id)}|{event.case.CaseNumber}> - _{event.case.Subject}_{assigned}:thread: ",
                f"```\n{output.short_description}\n```",
                *case_details_lines(event.case, facts),
            ]
        )
        original_message = await post_response(
            client=hctx.app.client,
            channel=channel,
            thread_ts=None,
            text=header,
        )

        if not original_message:
            logfire.error("Failed to post message, aborting")
            return

        message_to_link_to = SlackMessage(
            channel_id=channel,
            ts=original_message.data.get("ts"),
            text=output.message,
            thread_ts=None,
            to_user_id=case_owner_user_id,
        )

        await post_response(
            client=hctx.app.client,
            channel=channel,
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
