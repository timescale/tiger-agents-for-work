import asyncio
from datetime import timedelta
from pathlib import Path

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.listeners.harness import ListenerHarness
from tiger_agent.salesforce.types import (
    AgentFeedbackRatingEvent,
    CustomRuleMatchEvent,
    SalesforceAssignmentChangedEvent,
    SalesforceCaseStatusChangedEvent,
    SalesforceCreateNewCaseEvent,
    SalesforceFeedItemEvent,
)
from tiger_agent.slack.types import (
    SlackAppMentionEvent,
    SlackMessageEvent,
    SlackSalesforceCaseThreadMessageEvent,
)
from tiger_agent.tasks.handlers import (
    AgentFeedbackRatingHandler,
    CustomRuleMatchHandler,
    SalesforceAssignmentChangedHandler,
    SalesforceCaseStatusChangedHandler,
    SalesforceCreateCaseHandler,
    SalesforceFeedItemHandler,
    SlackSalesforceCaseThreadMessageHandler,
    SlackTaskHandler,
    TaskProcessor,
)
from tiger_agent.tasks.harness import TaskHarness
from tiger_agent.types import HarnessContext
from tiger_agent.utils import get_harness_ctx


class TigerApp:
    """Top-level entry point for running a Tiger Agent application.

    Combines a TigerAgent, TaskProcessor, ListenerHarness, and TaskHarness into
    a single object. Accepts either a ready-made HarnessContext or the individual
    parameters needed to build one.

    Simple usage with defaults:

        app = TigerApp()
        asyncio.run(app.run())

    Custom agent:

        agent = MyCoolAgent(model="anthropic:claude-sonnet-4-5-20250929")
        app = TigerApp(agent=agent)
        asyncio.run(app.run())

    Bring your own context (for full control):

        hctx = HarnessContext(app=..., pool=..., trigger=..., num_workers=10)
        agent = MyCoolAgent(...)
        app = TigerApp(agent=agent, hctx=hctx)
        asyncio.run(app.run())
    """

    def __init__(
        self,
        agent: TigerAgent | None = None,
        hctx: HarnessContext | None = None,
        # TigerAgent constructor args — only used when agent is not provided
        model: str = "anthropic:claude-sonnet-4-5-20250929",
        mcp_config_path: Path | None = None,
        prompt_config: list[Path] | None = None,
        rate_limit_allowed_requests: int | None = None,
        rate_limit_interval: timedelta = timedelta(minutes=1),
        # HarnessContext args — only used when hctx is not provided
        num_workers: int = 5,
        proactive_prompt_channels: list[str] | None = None,
        worker_sleep_seconds: int = 60,
        worker_min_jitter_seconds: int = -15,
        worker_max_jitter_seconds: int = 15,
        max_attempts: int = 3,
        max_age_minutes: int = 60,
        invisibility_minutes: int = 10,
    ):
        if hctx is None:
            hctx = get_harness_ctx(
                num_workers=num_workers,
                proactive_prompt_channels=proactive_prompt_channels,
                worker_sleep_seconds=worker_sleep_seconds,
                worker_min_jitter_seconds=worker_min_jitter_seconds,
                worker_max_jitter_seconds=worker_max_jitter_seconds,
                max_attempts=max_attempts,
                max_age_minutes=max_age_minutes,
                invisibility_minutes=invisibility_minutes,
            )

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
            CustomRuleMatchEvent, CustomRuleMatchHandler(hctx=hctx, agent=agent)
        )

        self._hctx = hctx
        self._listener_harness = ListenerHarness(hctx=hctx, task_processor=processor)
        self._task_harness = TaskHarness(processor, hctx=hctx)

    async def run(self) -> None:
        await self._hctx.pool.open(wait=True)
        async with asyncio.TaskGroup() as tasks:
            await self._task_harness.run(tasks)
            await self._listener_harness.start(tasks)
