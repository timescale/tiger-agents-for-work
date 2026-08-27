import logfire

from tiger_agent.db.utils import (
    get_salesforce_account_id_for_channel,
)
from tiger_agent.salesforce.types import SalesforceCreateNewCaseEvent
from tiger_agent.salesforce.utils import create_case
from tiger_agent.slack.utils import get_handle_link
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.handlers.utils import create_slack_thread_for_case
from tiger_agent.tasks.types import Task


class SalesforceCreateCaseHandler(TaskHandler):
    """
    Creates a Salesforce case from a Slack-initiated form submission and posts
    a confirmation message to the originating channel.
    """

    EVENT_TYPES = [SalesforceCreateNewCaseEvent]

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

        await create_slack_thread_for_case(
            hctx=hctx,
            case=new_case,
            channel=channel_to_respond,
            submitter=get_handle_link(event.user),
        )
