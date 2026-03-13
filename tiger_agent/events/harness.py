"""
Tiger Agent Event Processing Harness.

This module provides the core event processing infrastructure for Tiger Agent,
implementing a durable work queue system using PostgreSQL with TimescaleDB.
The harness manages Slack app_mention events through a multi-worker architecture
with atomic event claiming, retry logic, and automatic cleanup.

Key Components:
- EventHarness: Main orchestrator for event processing
- HarnessContext: Shared resources (Slack app, database pool, task group) for event processors
- Event/AppMentionEvent: Data models for Slack events
- Database integration with agent.event table as work queue
"""

import asyncio
import logging
import os
import random
from asyncio import QueueShutDown, TaskGroup
from collections.abc import Sequence

import logfire
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp

from tiger_agent.db.utils import (
    create_default_pool,
    delete_expired_events,
)
from tiger_agent.events.salesforce import SalesforceEventHandler
from tiger_agent.events.slack import SlackEventHandler
from tiger_agent.events.types import EventProcessor, HarnessContext
from tiger_agent.events.utils import process_events
from tiger_agent.migrations import runner
from tiger_agent.salesforce.types import SalesforceConfig

logger = logging.getLogger(__name__)


class EventHarness:
    """
    Core event processing harness for Tiger Agent with bounded concurrency and immediate responsiveness.

    The EventHarness orchestrates the entire event processing pipeline:
    1. Receives Slack app_mention events via Socket Mode
    2. Stores events durably in PostgreSQL (agent.event table)
    3. Coordinates multiple workers to claim and process events
    4. Handles retries, timeouts, and cleanup of expired events

    Key architectural features:

    **Bounded Concurrency**: Fixed number of worker tasks (num_workers) ensures predictable
    resource usage and prevents overwhelming downstream systems.

    **Immediate Event Handling**: When events arrive, exactly one worker is immediately "poked" via an
    asyncio.Queue trigger, ensuring events are processed without delay rather than
    waiting for the next polling cycle.

    **Atomic Event Claiming**: Multiple workers compete for events using agent.claim_event(),
    which atomically assigns events to exactly one worker, preventing duplicate processing.

    **Resilient Retry Logic**: Failed/missed events are automatically retried through:
    - Periodic worker polling (with jitter to prevent thundering herd)
    - Automatic cleanup of expired/stuck events
    - Visibility thresholds that make failed events available for retry

    The harness implements a work queue pattern where:
    - Events are atomically claimed by workers using agent.claim_event()
    - Failed processing leaves events available for retry
    - Successful processing moves events to agent.event_hist
    - Expired events are automatically cleaned up

    Args:
        event_processor: Callback function that processes claimed events
        app: Optional Slack AsyncApp (creates default if None)
        pool: Optional database connection pool (creates default if None)
        worker_sleep_seconds: Base sleep time between worker runs
        worker_min_jitter_seconds: Minimum random jitter for worker sleep
        worker_max_jitter_seconds: Maximum random jitter for worker sleep
        max_attempts: Maximum retry attempts per event
        max_age_minutes: Maximum age before events are expired
        invisibility_minutes: How long claimed events remain invisible
        num_workers: Number of concurrent worker tasks (bounded concurrency)
        slack_bot_token: Slack bot token (uses SLACK_BOT_TOKEN env if None)
        slack_app_token: Slack app token (uses SLACK_APP_TOKEN env if None)
        salesforce_config: Optional Salesforce config
    """

    def __init__(
        self,
        event_processor: EventProcessor,
        app: AsyncApp | None = None,
        pool: AsyncConnectionPool | None = None,
        worker_sleep_seconds: int = 60,
        worker_min_jitter_seconds: int = -15,
        worker_max_jitter_seconds: int = 15,
        max_attempts: int = 3,
        max_age_minutes: int = 60,
        invisibility_minutes: int = 10,
        num_workers: int = 5,
        slack_bot_token: str | None = None,
        slack_app_token: str | None = None,
        salesforce_config: SalesforceConfig | None = None,
        proactive_prompt_channels: Sequence[str] | None = None,
    ):
        self._task_group: TaskGroup | None = None
        self._pool = pool if pool is not None else create_default_pool(num_workers)
        self._trigger = asyncio.Queue()
        self._event_processor = event_processor
        self._worker_sleep_seconds = worker_sleep_seconds
        self._worker_min_jitter_seconds = worker_min_jitter_seconds
        self._worker_max_jitter_seconds = worker_max_jitter_seconds
        self._max_attempts = max_attempts
        self._max_age_minutes = max_age_minutes
        self._num_workers = num_workers
        self._invisibility_minutes = invisibility_minutes
        self._slack_bot_token = slack_bot_token or os.getenv("SLACK_BOT_TOKEN")
        assert self._slack_bot_token is not None, "no SLACK_BOT_TOKEN found"
        self._slack_app_token = slack_app_token or os.getenv("SLACK_APP_TOKEN")
        assert self._slack_app_token is not None, "no SLACK_APP_TOKEN found"
        self._proactive_prompt_channels = set(proactive_prompt_channels or [])
        assert worker_sleep_seconds > 0
        assert worker_sleep_seconds - worker_min_jitter_seconds > 0
        assert worker_max_jitter_seconds > worker_min_jitter_seconds
        self._app = (
            app
            if app is not None
            else AsyncApp(
                token=self._slack_bot_token,
                ignoring_self_events_enabled=False,
            )
        )

        self._salesforce_config = salesforce_config

    def _make_harness_context(self) -> HarnessContext:
        """Create a context object for event processors.

        Returns:
            HarnessContext: Context containing Slack app, database pool, and task group
        """
        return HarnessContext(
            self._app,
            self._pool,
            self._slack_bot_token,
            self._slack_app_token,
            self._trigger,
        )

    def _calc_worker_sleep(self) -> int:
        """Calculate sleep duration for worker with random jitter.

        Adds random jitter to the base sleep time to prevent workers
        from synchronizing and creating thundering herd effects.

        Returns:
            int: Sleep duration in seconds with jitter applied
        """
        jitter = random.randint(
            self._worker_min_jitter_seconds, self._worker_max_jitter_seconds
        )
        return self._worker_sleep_seconds + jitter

    async def _worker(self, worker_id: int, initial_sleep_seconds: int):
        """Main worker loop for processing events.

        Each worker runs independently, either triggered by new events
        or by timeout. Workers are initially staggered to distribute
        load and prevent thundering herd effects.

        Args:
            worker_id: Unique identifier for this worker
            initial_sleep_seconds: Initial delay before starting work
        """

        async def worker_run(reason: str):
            with logfire.span("worker_run", worker_id=worker_id, reason=reason) as _:
                await process_events(
                    self._event_processor,
                    self._make_harness_context(),
                    self._max_attempts,
                    self._invisibility_minutes,
                )
                await delete_expired_events(
                    pool=self._pool,
                    max_attempts=self._max_attempts,
                    max_age_minutes=self._max_age_minutes,
                )

        # initial staggering of workers
        if initial_sleep_seconds > 0:
            logger.info(
                "worker initial sleep",
                extra={
                    "worker_id": worker_id,
                    "initial_sleep_seconds": initial_sleep_seconds,
                },
            )
            await asyncio.sleep(initial_sleep_seconds)

        logger.info("starting worker", extra={"worker_id": worker_id})
        while True:
            try:
                await asyncio.wait_for(
                    self._trigger.get(), timeout=self._calc_worker_sleep()
                )
                await worker_run("triggered")
            except TimeoutError:
                await worker_run("timeout")
            except QueueShutDown:
                return

    def _worker_args(self, num_workers: int) -> list[tuple[int, int]]:
        """Generate worker arguments with staggered start times.

        Creates a list of (worker_id, initial_sleep) tuples where the first
        worker starts immediately and subsequent workers are randomly staggered.

        Args:
            num_workers: Number of workers to create

        Returns:
            list[tuple[int, int]]: List of (worker_id, initial_sleep_seconds) pairs
        """
        initial_sleeps: list[int] = [0]  # first worker starts immediately
        # pick num_workers - 1 unique initial sleep values
        initial_sleeps.extend(
            random.sample(range(1, self._worker_sleep_seconds), num_workers - 1)
        )
        return [
            (worker_id, initial_sleep)
            for worker_id, initial_sleep in enumerate(initial_sleeps)
        ]

    async def run(self):
        """Start the event harness and run indefinitely.

        This method:
        1. Opens the database connection pool
        2. Runs database migrations
        3. Creates and starts worker tasks
        4. Sets up Slack event handling
        5. Starts the Slack Socket Mode connection

        All tasks run concurrently in a TaskGroup. The method blocks
        until interrupted or an unhandled exception occurs.
        """
        await self._pool.open(wait=True)
        hctx = self._make_harness_context()
        slack_event_handler = SlackEventHandler(
            hctx=hctx,
            event_processor=self._event_processor,
            proactive_prompt_channels=self._proactive_prompt_channels,
        )

        salesforce_event_handler = SalesforceEventHandler(hctx=hctx)

        async with asyncio.TaskGroup() as tasks:
            async with self._pool.connection() as con:
                await runner.migrate_db(con)

            logger.info(f"creating {self._num_workers} workers")
            for worker_id, initial_sleep in self._worker_args(self._num_workers):
                logger.info("creating worker", extra={"worker_id": worker_id})
                tasks.create_task(self._worker(worker_id, initial_sleep))

            await slack_event_handler.start(tasks=tasks)

            if self._salesforce_config and self._salesforce_config.is_valid():
                await salesforce_event_handler.start(tasks=tasks)
