from asyncio import TaskGroup

from tiger_agent.listeners import Listener
from tiger_agent.listeners.salesforce import SalesforceListener
from tiger_agent.listeners.slack import SlackListener
from tiger_agent.tasks.types import TaskProcessor
from tiger_agent.types import Context


class ListenerHarness(Listener):
    def __init__(
        self, ctx: Context, task_processor: TaskProcessor
    ):  # TODO: remove dependence on taskprocessor
        self._listeners: list[Listener] = [
            SlackListener(ctx=ctx, task_processor=task_processor)
        ]

        if ctx.salesforce_client:
            self._listeners.append(SalesforceListener(ctx=ctx))

    async def start(self, tasks: TaskGroup):

        for listener in self._listeners:
            await listener.start(tasks=tasks)
