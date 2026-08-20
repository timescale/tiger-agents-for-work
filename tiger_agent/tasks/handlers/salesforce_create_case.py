import logfire

from tiger_agent.agent.utils import summarize_new_case
from tiger_agent.db.utils import (
    add_salesforce_case_thread,
    get_salesforce_account_id_for_channel,
)
from tiger_agent.salesforce.constants import SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD
from tiger_agent.salesforce.types import SalesforceCreateNewCaseEvent
from tiger_agent.salesforce.utils import create_case, update_case
from tiger_agent.slack.utils import add_quote_block, get_handle_link, post_response
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task


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
        subject = new_case.Subject or ""
        short_description = await summarize_new_case(
            subject=subject, description=new_case.Description or ""
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
                    f"_Subject:_ `{subject[0:1000]}`",
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

        update_case(
            hctx.salesforce_client,
            new_case.Id,
            {SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD: permalink},
        )

        logfire.info(
            "Updated Salesforce case to include the customer thread link",
            extra={"permalink": permalink},
        )
