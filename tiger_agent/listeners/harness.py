from asyncio import TaskGroup

from tiger_agent.listeners import Listener
from tiger_agent.listeners.salesforce import SalesforceListener
from tiger_agent.listeners.slack import SlackListener
from tiger_agent.tasks.types import TaskProcessor
from tiger_agent.types import HarnessContext


class ListenerHarness(Listener):
    def __init__(
        self, hctx: HarnessContext, task_processor: TaskProcessor
    ):  # TODO: remove dependence on taskprocessor
        self._listeners: list[Listener] = [
            SlackListener(hctx=hctx, task_processor=task_processor)
        ]

        if hctx.salesforce_client:
            self._listeners.append(SalesforceListener(hctx=hctx))

    async def start(self, tasks: TaskGroup):

        for listener in self._listeners:
            await listener.start(tasks=tasks)
