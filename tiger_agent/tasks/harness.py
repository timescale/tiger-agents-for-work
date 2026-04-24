"""
Tiger Agent Task Processing Harness.

This module provides the core task processing infrastructure for Tiger Agent,
implementing a durable work queue system using PostgreSQL with TimescaleDB.
The harness manages tasks through a multi-worker architecture
with atomic task claiming, retry logic, and automatic cleanup.

Key Components:
- TaskHarness: Main orchestrator for task processing
- Context: Shared resources (Slack app, database pool, trigger) for listeners and processors
- Task: Data model for work queue items
- Database integration with agent.event table as work queue
"""

import asyncio
import logging
import random
from asyncio import QueueShutDown, TaskGroup

from tiger_agent.db.utils import delete_expired_events
from tiger_agent.migrations import runner
from tiger_agent.tasks.types import Context, TaskProcessor
from tiger_agent.tasks.utils import process_tasks

logger = logging.getLogger(__name__)


class TaskHarness:
    """
    Core task processing harness for Tiger Agent with bounded concurrency and immediate responsiveness.

    The TaskHarness orchestrates the task processing pipeline:
    1. Stores tasks durably in PostgreSQL (agent.event table)
    2. Coordinates multiple workers to claim and process tasks
    3. Handles retries, timeouts, and cleanup of expired tasks

    Key architectural features:

    **Bounded Concurrency**: Fixed number of worker tasks (num_workers) ensures predictable
    resource usage and prevents overwhelming downstream systems.

    **Immediate Task Handling**: When tasks arrive, exactly one worker is immediately "poked" via an
    asyncio.Queue trigger on the Context, ensuring tasks are processed without delay rather than
    waiting for the next polling cycle.

    **Atomic Task Claiming**: Multiple workers compete for tasks using agent.claim_event(),
    which atomically assigns tasks to exactly one worker, preventing duplicate processing.

    **Resilient Retry Logic**: Failed/missed tasks are automatically retried through:
    - Periodic worker polling (with jitter to prevent thundering herd)
    - Automatic cleanup of expired/stuck tasks
    - Visibility thresholds that make failed tasks available for retry

    Args:
        task_processor: Callback function that processes claimed tasks
        ctx: Shared context providing app, pool, trigger, and optional salesforce client
        worker_sleep_seconds: Base sleep time between worker runs
        worker_min_jitter_seconds: Minimum random jitter for worker sleep
        worker_max_jitter_seconds: Maximum random jitter for worker sleep
        max_attempts: Maximum retry attempts per task
        max_age_minutes: Maximum age before tasks are expired
        invisibility_minutes: How long claimed tasks remain invisible
        num_workers: Number of concurrent worker tasks (bounded concurrency)
    """

    def __init__(
        self,
        task_processor: TaskProcessor,
        ctx: Context,
        worker_sleep_seconds: int = 60,
        worker_min_jitter_seconds: int = -15,
        worker_max_jitter_seconds: int = 15,
        max_attempts: int = 3,
        max_age_minutes: int = 60,
        invisibility_minutes: int = 10,
        num_workers: int = 5,
    ):
        self._task_processor = task_processor
        self._ctx = ctx
        self._worker_sleep_seconds = worker_sleep_seconds
        self._worker_min_jitter_seconds = worker_min_jitter_seconds
        self._worker_max_jitter_seconds = worker_max_jitter_seconds
        self._max_attempts = max_attempts
        self._max_age_minutes = max_age_minutes
        self._num_workers = num_workers
        self._invisibility_minutes = invisibility_minutes
        assert worker_sleep_seconds > 0
        assert worker_sleep_seconds - worker_min_jitter_seconds > 0
        assert worker_max_jitter_seconds > worker_min_jitter_seconds

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
        """Main worker loop for processing tasks.

        Each worker runs independently, either triggered by new tasks
        or by timeout. Workers are initially staggered to distribute
        load and prevent thundering herd effects.

        Args:
            worker_id: Unique identifier for this worker
            initial_sleep_seconds: Initial delay before starting work
        """

        async def worker_run():
            await process_tasks(
                self._task_processor,
                self._ctx,
                self._max_attempts,
                self._invisibility_minutes,
            )
            await delete_expired_events(
                pool=self._ctx.pool,
                max_attempts=self._max_attempts,
                max_age_minutes=self._max_age_minutes,
            )

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
                    self._ctx.trigger.get(), timeout=self._calc_worker_sleep()
                )
                await worker_run()
            except TimeoutError:
                await worker_run()
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
        initial_sleeps.extend(
            random.sample(range(1, self._worker_sleep_seconds), num_workers - 1)
        )
        return [
            (worker_id, initial_sleep)
            for worker_id, initial_sleep in enumerate(initial_sleeps)
        ]

    async def run(self, tasks: TaskGroup):
        """Run the harness workers within an existing TaskGroup.

        Runs database migrations then starts all workers. Designed to be called
        alongside listener start() calls within the same TaskGroup so that
        listeners and workers run concurrently.

        Args:
            tasks: The asyncio TaskGroup to create worker tasks in
        """
        async with self._ctx.pool.connection() as con:
            await runner.migrate_db(con)

        logger.info(f"creating {self._num_workers} workers")
        for worker_id, initial_sleep in self._worker_args(self._num_workers):
            logger.info("creating worker", extra={"worker_id": worker_id})
            tasks.create_task(self._worker(worker_id, initial_sleep))
