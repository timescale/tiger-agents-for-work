import asyncio
from datetime import timedelta
from pathlib import Path

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.listeners.harness import ListenerHarness
from tiger_agent.salesforce.types import (
    SalesforceAssignmentChangedEvent,
    SalesforceCaseCreatedEvent,
    SalesforceCaseStatusChangedEvent,
    SalesforceCreateNewCaseEvent,
    SalesforceFeedItemEvent,
    UserDefinedRuleMatch,
)
from tiger_agent.slack.types import (
    AgentFeedbackRatingEvent,
    AgentFeedbackRequestReminderEvent,
    SlackAppMentionEvent,
    SlackMessageEvent,
    SlackSalesforceCaseThreadMessageEvent,
)
from tiger_agent.tasks.handlers import (
    AgentFeedbackRatingHandler,
    AgentFeedbackRequestReminderHandler,
    SalesforceAssignmentChangedHandler,
    SalesforceCaseCreatedHandler,
    SalesforceCaseStatusChangedHandler,
    SalesforceCreateCaseHandler,
    SalesforceFeedItemHandler,
    SlackSalesforceCaseThreadMessageHandler,
    SlackTaskHandler,
    TaskProcessor,
    UserDefinedRuleMatchHandler,
)
from tiger_agent.tasks.harness import TaskHarness
from tiger_agent.types import HarnessContext


class TigerApp:
    """Top-level entry point for running a Tiger Agent application.

    Combines a TigerAgent, TaskProcessor, ListenerHarness, and TaskHarness into
    a single object. Requires a HarnessContext (use ``HarnessContext.create()``
    to build a default one).

    Simple usage with defaults:

        app = await TigerApp.create()
        await app.run()

    Custom agent:

        agent = MyCoolAgent(model="anthropic:claude-sonnet-4-5-20250929")
        app = await TigerApp.create(agent=agent)
        await app.run()

    Bring your own context (for full control):

        hctx = await HarnessContext.create(num_workers=10)
        agent = MyCoolAgent(...)
        app = TigerApp(hctx=hctx, agent=agent)
        await app.run()
    """

    def __init__(
        self,
        hctx: HarnessContext,
        agent: TigerAgent | None = None,
        *,
        model: str = "anthropic:claude-sonnet-4-5-20250929",
        mcp_config_path: Path | None = None,
        prompt_config: list[Path] | None = None,
        rate_limit_allowed_requests: int | None = None,
        rate_limit_interval: timedelta = timedelta(minutes=1),
    ):
        if agent is None:
            agent = TigerAgent(
                model=model,
                mcp_config_path=mcp_config_path,
                prompt_config=prompt_config,
                rate_limit_allowed_requests=rate_limit_allowed_requests,
                rate_limit_interval=rate_limit_interval,
            )

        processor = TaskProcessor(hctx=hctx, agent=agent)
        processor.register(
            [SlackAppMentionEvent, SlackMessageEvent],
            SlackTaskHandler(hctx=hctx, agent=agent),
        )
        processor.register(
            SalesforceCaseCreatedEvent,
            SalesforceCaseCreatedHandler(hctx=hctx, agent=agent),
        )
        processor.register(
            SalesforceAssignmentChangedEvent,
            SalesforceAssignmentChangedHandler(hctx=hctx, agent=agent),
        )
        processor.register(
            SalesforceCreateNewCaseEvent, SalesforceCreateCaseHandler(hctx=hctx)
        )
        processor.register(
            SalesforceFeedItemEvent, SalesforceFeedItemHandler(hctx=hctx)
        )
        processor.register(
            SlackSalesforceCaseThreadMessageEvent,
            SlackSalesforceCaseThreadMessageHandler(hctx=hctx),
        )
        processor.register(
            SalesforceCaseStatusChangedEvent,
            SalesforceCaseStatusChangedHandler(hctx=hctx),
        )
        processor.register(
            AgentFeedbackRatingEvent, AgentFeedbackRatingHandler(hctx=hctx)
        )
        processor.register(
            AgentFeedbackRequestReminderEvent,
            AgentFeedbackRequestReminderHandler(hctx=hctx),
        )
        processor.register(UserDefinedRuleMatch, UserDefinedRuleMatchHandler(hctx=hctx))

        self._hctx = hctx
        self._listener_harness = ListenerHarness(hctx=hctx, task_processor=processor)
        self._task_harness = TaskHarness(processor, hctx=hctx)

    @classmethod
    async def create(
        cls,
        agent: TigerAgent | None = None,
        *,
        model: str = "anthropic:claude-sonnet-4-5-20250929",
        mcp_config_path: Path | None = None,
        prompt_config: list[Path] | None = None,
        rate_limit_allowed_requests: int | None = None,
        rate_limit_interval: timedelta = timedelta(minutes=1),
        proactive_prompt_channels: list[str] | None = None,
        num_workers: int = 5,
        worker_sleep_seconds: int = 60,
        worker_min_jitter_seconds: int = -15,
        worker_max_jitter_seconds: int = 15,
        max_attempts: int = 3,
        max_age_minutes: int = 60,
        invisibility_minutes: int = 10,
    ) -> "TigerApp":
        hctx = await HarnessContext.create(
            proactive_prompt_channels=proactive_prompt_channels,
            num_workers=num_workers,
            worker_sleep_seconds=worker_sleep_seconds,
            worker_min_jitter_seconds=worker_min_jitter_seconds,
            worker_max_jitter_seconds=worker_max_jitter_seconds,
            max_attempts=max_attempts,
            max_age_minutes=max_age_minutes,
            invisibility_minutes=invisibility_minutes,
        )
        return cls(
            hctx=hctx,
            agent=agent,
            model=model,
            mcp_config_path=mcp_config_path,
            prompt_config=prompt_config,
            rate_limit_allowed_requests=rate_limit_allowed_requests,
            rate_limit_interval=rate_limit_interval,
        )

    async def run(self) -> None:
        await self._hctx.pool.open(wait=True)
        async with asyncio.TaskGroup() as tasks:
            await self._task_harness.run(tasks)
            await self._listener_harness.start(tasks)
