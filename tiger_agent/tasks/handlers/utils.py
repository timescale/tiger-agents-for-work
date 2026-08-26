import logfire

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.utils import create_agent_and_context, summarize_new_case
from tiger_agent.db.utils import (
    add_salesforce_case_thread,
)
from tiger_agent.salesforce.constants import (
    SALESFORCE_CASE_CHANNEL,
    SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD,
    SALESFORCE_SLACK_THREAD_FIELD,
)
from tiger_agent.salesforce.types import CaseData, SalesforceCaseCreatedEvent
from tiger_agent.salesforce.utils import (
    add_internal_case_post,
    create_case_url,
    update_case,
)
from tiger_agent.slack.types import SlackMessage
from tiger_agent.slack.utils import add_quote_block, post_response, request_feedback
from tiger_agent.tasks.handlers.base import AGENT_USAGE_LIMITS
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext


async def create_slack_thread_for_case(
    hctx: HarnessContext, case: CaseData, channel: str, submitter: str | None = None
) -> None:
    subject = case.Subject or ""
    short_description = await summarize_new_case(
        subject=subject, description=case.Description or ""
    )
    submitter = (
        submitter or case.SuppliedName or case.SuppliedEmail or case.ContactEmail
    )
    response = await post_response(
        client=hctx.app.client,
        channel=channel,
        thread_ts=None,
        text="\n".join(
            [
                "*Support Case Created*",
                *([f"_Submitter:_ {submitter}"] if submitter else []),
                f"_Case Number:_ `{case.CaseNumber}`",
                f"_Subject:_ `{subject[0:1000]}`",
                *(
                    [f"_Project Id:_: `{case.Cloud_Project_ID__c}`"]
                    if case.Cloud_Project_ID__c
                    else []
                ),
                *(
                    [f"_Service Id:_: `{case.Cloud_Service_ID__c}`"]
                    if case.Cloud_Service_ID__c
                    else []
                ),
                "_Description:_",
                add_quote_block(short_description),
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
        channel_id=channel,
        case_id=case.Id,
    )

    if not SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD:
        logfire.error("SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD not specified, skipping")
        return

    result = await hctx.app.client.chat_getPermalink(
        channel=channel,
        message_ts=new_case_thread_ts,
    )
    permalink = result.data.get("permalink")

    update_case(
        hctx.salesforce_client,
        case.Id,
        {SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD: permalink},
    )

    logfire.info(
        "Updated Salesforce case to include the customer thread link",
        extra={"permalink": permalink},
    )


async def detect_spam_case(hctx: HarnessContext, task: Task, agent: TigerAgent) -> None:
    event: SalesforceCaseCreatedEvent = task.event

    # at this time we only care about filtering email messages
    if event.case.Origin.lower() != "email":
        return

    agent_and_ctx = await create_agent_and_context(
        hctx=hctx,
        task=task,
        agent=agent,
        channel_to_respond=SALESFORCE_CASE_CHANNEL,
    )

    response = await agent_and_ctx.agent.run(
        user_prompt=agent_and_ctx.user_prompt,
        deps=agent_and_ctx.ctx,
        usage_limits=AGENT_USAGE_LIMITS,
    )

    if not response.output.is_spam:
        return

    logfire.info(
        "Salesforce case identified as spam",
    )

    original_message = await post_response(
        client=hctx.app.client,
        channel=SALESFORCE_CASE_CHANNEL,
        thread_ts=None,
        text=f"*Spam Detected* <{create_case_url(event.case.Id)}|{event.case.CaseNumber}> - _{event.case.Subject}_",
    )

    message_thread = original_message.data.get("ts")
    if not message_thread:
        raise Exception(
            "Could not create a thread for the customer-created Salesforce case"
        )

    message_to_link_to = SlackMessage(
        channel_id=SALESFORCE_CASE_CHANNEL,
        ts=message_thread,
        text=response.output.message,
        thread_ts=None,
    )

    if SALESFORCE_SLACK_THREAD_FIELD:
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

    add_internal_case_post(
        salesforce_client=hctx.salesforce_client,
        case_id=event.case.Id,
        body=response.output.short_description,
    )
    request_feedback(
        hctx.app.client,
        channel=message_to_link_to.channel_id,
        thread_ts=message_to_link_to.ts,
    )

    update_case(
        hctx.salesforce_client,
        event.case.Id,
        {"Status": "Spam", "Type": "Spam"},
    )
