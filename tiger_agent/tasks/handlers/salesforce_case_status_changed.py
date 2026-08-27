import logfire

from tiger_agent.salesforce.types import SalesforceCaseStatusChangedEvent
from tiger_agent.slack.utils import post_response
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task


class SalesforceCaseStatusChangedHandler(TaskHandler):
    """
    Called when a Salesforce case status changes.
    """

    EVENT_TYPES = [SalesforceCaseStatusChangedEvent]

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
