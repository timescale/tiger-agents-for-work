import logfire

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.salesforce.constants import (
    SALESFORCE_ENABLE_SPAM_FILTERING,
)
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.handlers.utils import detect_spam_case
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext


class SalesforceCaseCreatedHandler(TaskHandler):
    """
    Runs the agent to determine if the case is spam.
    We handle legitimate new cases with the SalesforceAssignmentChangedHandler
    as, at that point we have a assignee and spam should have been filtered out
    So this handler is strictly to detect spam cases
    """

    def __init__(self, hctx: HarnessContext, agent: TigerAgent) -> None:
        super().__init__(hctx)
        self._agent = agent

    @logfire.instrument("SalesforceCaseCreatedHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        if not SALESFORCE_ENABLE_SPAM_FILTERING:
            return

        await detect_spam_case(hctx=self._hctx, task=task, agent=self._agent)
