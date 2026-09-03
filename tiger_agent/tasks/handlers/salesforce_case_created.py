import logfire

from tiger_agent.db.utils import get_slack_channel_for_salesforce_account_id
from tiger_agent.salesforce.constants import (
    SALESFORCE_ENABLE_SPAM_FILTERING,
)
from tiger_agent.salesforce.types import SalesforceCaseCreatedEvent
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.handlers.utils import (
    create_slack_thread_for_case,
    detect_spam_case,
)
from tiger_agent.tasks.types import Task


class SalesforceCaseCreatedHandler(TaskHandler):
    """
    Determines whether a newly created case is spam.

    Legitimate new cases are handled by SalesforceAssignmentChangedHandler once
    an assignee exists, so this handler is strictly spam triage. It runs a small
    tool-free agent (see ``assess_case_for_spam``) rather than the full Eon
    agent -- deciding that an email is a vendor solicitation does not warrant a
    case investigation.
    """

    EVENT_TYPES = [SalesforceCaseCreatedEvent]

    @logfire.instrument("SalesforceCaseCreatedHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        event: SalesforceCaseCreatedEvent = task.event
        if (event.case.Origin or "").lower() != "slack" and event.case.AccountId:
            channel_id_for_account = await get_slack_channel_for_salesforce_account_id(
                pool=self._hctx.pool, account_id=event.case.AccountId
            )

            if channel_id_for_account:
                logfire.info(
                    "New case created for an account linked to Slack channel",
                    extra={
                        "channel_id_for_account": channel_id_for_account,
                        "case": event.case,
                    },
                )
                await create_slack_thread_for_case(
                    hctx=self._hctx, case=event.case, channel=channel_id_for_account
                )

                return

        if SALESFORCE_ENABLE_SPAM_FILTERING:
            await detect_spam_case(hctx=self._hctx, task=task, agent=self._agent)
