from asyncio import TaskGroup

import logfire

from tiger_agent.listeners import Listener
from tiger_agent.listeners.salesforce import SalesforceListener
from tiger_agent.listeners.slack import SlackListener
from tiger_agent.salesforce.constants import DISABLE_SALESFORCE_EVENT_HANDLING
from tiger_agent.tasks.handlers import TaskProcessor
from tiger_agent.types import HarnessContext


class ListenerHarness(Listener):
    def __init__(
        self, hctx: HarnessContext, task_processor: TaskProcessor
    ):  # TODO: remove dependence on taskprocessor
        self._listeners: list[Listener] = [
            SlackListener(hctx=hctx, task_processor=task_processor)
        ]

        if hctx.salesforce_client and not DISABLE_SALESFORCE_EVENT_HANDLING:
            self._listeners.append(SalesforceListener(hctx=hctx))
        elif hctx.salesforce_client:
            logfire.info(
                "DISABLE_SALESFORCE_EVENT_HANDLING is set; not starting the "
                "Salesforce listener or missed-case pollers"
            )

    async def start(self, tasks: TaskGroup):

        for listener in self._listeners:
            await listener.start(tasks=tasks)
