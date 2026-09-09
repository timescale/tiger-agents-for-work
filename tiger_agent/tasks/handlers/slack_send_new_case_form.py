import logfire

from tiger_agent.slack.types import (
    SlackRequestNewCaseFormEvent,
)
from tiger_agent.slack.utils import (
    add_reaction,
)
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.handlers.utils import send_new_salesforce_case_workflow_form
from tiger_agent.tasks.types import Task


class SlackSendNewCaseFormHandler(TaskHandler):
    EVENT_TYPES = [SlackRequestNewCaseFormEvent]

    @logfire.instrument("SlackSendNewCaseFormHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SlackRequestNewCaseFormEvent = task.event

        await send_new_salesforce_case_workflow_form(
            slack_client=hctx.app.client,
            salesforce_client=hctx.salesforce_client,
            channel=event.channel,
            user=event.user,
            pool=hctx.pool,
        )
        await add_reaction(
            hctx.app.client, event.channel, event.trigger_message_ts, "white_check_mark"
        )
