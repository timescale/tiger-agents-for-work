import asyncio
import logging
import signal
from datetime import timedelta
from pathlib import Path

from tiger_agent.agent.constants import USER_DEFINED_EVENTS_ENABLED
from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.listeners.harness import ListenerHarness
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
    TaskHandler,
    TaskProcessor,
    UserDefinedRuleMatchHandler,
)
from tiger_agent.tasks.harness import TaskHarness
from tiger_agent.types import HarnessContext

logger = logging.getLogger(__name__)

# tiger-agents-deploy sets terminationGracePeriodSeconds to 900; stay under it so
# in-flight work is cancelled by us (and released for retry) rather than SIGKILLed.
DEFAULT_SHUTDOWN_GRACE_SECONDS = 840

_HANDLERS: list[type[TaskHandler]] = [
    SlackTaskHandler,
    SalesforceCaseCreatedHandler,
    SalesforceAssignmentChangedHandler,
    SalesforceCreateCaseHandler,
    SalesforceFeedItemHandler,
    SlackSalesforceCaseThreadMessageHandler,
    SalesforceCaseStatusChangedHandler,
    AgentFeedbackRatingHandler,
    AgentFeedbackRequestReminderHandler,
    *([UserDefinedRuleMatchHandler] if USER_DEFINED_EVENTS_ENABLED else []),
]


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

    Soft shutdown:

        ``run()`` handles SIGTERM/SIGINT. On the first signal the app stops
        claiming new tasks and disconnects its listeners, but lets in-flight
        tasks finish. If they have not finished after ``shutdown_grace_seconds``
        (or a second signal arrives) they are cancelled; their events stay in
        the queue and become visible to other instances for retry.
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
        shutdown_grace_seconds: int = DEFAULT_SHUTDOWN_GRACE_SECONDS,
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
        for handler_cls in _HANDLERS:
            processor.register(
                handler_cls.EVENT_TYPES, handler_cls(hctx=hctx, agent=agent)
            )

        self._hctx = hctx
        self._listener_harness = ListenerHarness(hctx=hctx, task_processor=processor)
        self._task_harness = TaskHarness(processor, hctx=hctx)
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._main_task: asyncio.Task | None = None
        self._deadline: asyncio.TimerHandle | None = None
        self._forced = False

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
        shutdown_grace_seconds: int = DEFAULT_SHUTDOWN_GRACE_SECONDS,
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
            shutdown_grace_seconds=shutdown_grace_seconds,
        )

    async def run(self) -> None:
        await self._hctx.pool.open(wait=True)
        loop = asyncio.get_running_loop()
        self._main_task = asyncio.current_task()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_shutdown, sig)
        try:
            async with asyncio.TaskGroup() as tasks:
                await self._task_harness.run(tasks)
                await self._listener_harness.start(tasks)
        except asyncio.CancelledError:
            if not self._forced:
                raise
            self._main_task.uncancel()
            logger.warning("shutdown grace period exceeded; in-flight work cancelled")
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
            if self._deadline is not None:
                self._deadline.cancel()
            await self._hctx.pool.close()
        logger.info("tiger agent stopped")

    def _request_shutdown(self, sig: signal.Signals) -> None:
        """Signal handler: begin a soft shutdown, or force one on a repeat signal."""
        if self._hctx.shutdown.is_set():
            logger.warning(
                "received %s during shutdown; cancelling in-flight work now", sig.name
            )
            self._force_shutdown()
            return

        logger.info(
            "received %s; finishing in-flight tasks before exiting",
            sig.name,
            extra={"grace_seconds": self._shutdown_grace_seconds},
        )
        self._hctx.shutdown.set()
        # wake idle workers so they see the flag instead of sleeping out their poll interval
        for _ in range(self._hctx.num_workers):
            self._hctx.trigger.put_nowait(None)
        self._deadline = asyncio.get_running_loop().call_later(
            self._shutdown_grace_seconds, self._force_shutdown
        )

    def _force_shutdown(self) -> None:
        """Cancel the TaskGroup (and so every worker and listener) immediately."""
        self._forced = True
        if self._main_task is not None and not self._main_task.done():
            self._main_task.cancel()
