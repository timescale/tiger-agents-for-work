import logfire

from tiger_agent.agent.utils import summarize_new_case
from tiger_agent.db.utils import (
    add_salesforce_case_thread,
)
from tiger_agent.salesforce.constants import SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD
from tiger_agent.salesforce.types import CaseData
from tiger_agent.salesforce.utils import update_case
from tiger_agent.slack.utils import add_quote_block, post_response
from tiger_agent.types import HarnessContext


async def create_slack_thread_for_case(
    hctx: HarnessContext, case: CaseData, channel: str, submitter: str | None = None
) -> None:
    subject = case.Subject or ""
    short_description = await summarize_new_case(
        subject=subject, description=case.Description or ""
    )
    submitter = submitter or case.ContactEmail
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
