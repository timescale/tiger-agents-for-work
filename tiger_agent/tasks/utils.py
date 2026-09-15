import asyncio
import contextlib
import logging

import logfire

from tiger_agent.db.utils import claim_event, delete_event, extend_event_visibility
from tiger_agent.tasks.handlers import TaskProcessor
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext

logger = logging.getLogger(__name__)


# How often to extend a claimed task's lease, as a fraction of the
# invisibility window, floored so a tiny window cannot hammer the database.
HEARTBEAT_FRACTION = 0.5
HEARTBEAT_MIN_SECONDS = 30.0


def heartbeat_interval_seconds(invisibility_minutes: int) -> float:
    return max(HEARTBEAT_MIN_SECONDS, invisibility_minutes * 60 * HEARTBEAT_FRACTION)


async def _heartbeat(hctx: HarnessContext, task: Task) -> None:
    """Keep re-leasing `task` until cancelled or until the lease is lost.

    A lost lease means another worker has already re-claimed the task; the
    current run carries on (aborting would waste the work done so far and the
    duplicate is already unavoidable) but we stop touching the row.
    """
    interval = heartbeat_interval_seconds(hctx.invisibility_minutes)
    while True:
        await asyncio.sleep(interval)
        try:
            held = await extend_event_visibility(
                pool=hctx.pool,
                event_id=task.id,
                attempts=task.attempts,
                invisibility_minutes=hctx.invisibility_minutes,
            )
        except Exception:
            logfire.exception(
                "Failed to extend task lease; will retry", task_id=task.id
            )
            continue
        if not held:
            logfire.warn(
                "Lost task lease; another worker has re-claimed it",
                task_id=task.id,
                attempts=task.attempts,
            )
            return


async def process_task(
    task_processor: TaskProcessor, hctx: HarnessContext, task: Task
) -> bool:
    """Process a single claimed task.

    Calls the registered task processor with the task and context while a
    heartbeat keeps the task's lease from expiring. On success, marks the
    task as completed. On failure, leaves the task in the queue for retry by
    other workers.

    Args:
        task: The claimed task to process

    Returns:
        bool: True if processing succeeded, False if it failed
    """
    with logfire.span("process_task", task=task) as _:
        heartbeat = asyncio.create_task(_heartbeat(hctx, task))
        try:
            await task_processor(hctx, task)
            await delete_event(pool=hctx.pool, event=task)
            return True
        except Exception as e:
            logger.exception(
                "task processing failed", extra={"task_id": task.id}, exc_info=e
            )
            # Task remains in database for retry
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        return False


async def process_tasks(
    task_processor: TaskProcessor,
    hctx: HarnessContext,
    max_attempts: int,
    invisibility_minutes: int,
):
    """Process available tasks in a batch.

    Attempts to claim and process up to 20 tasks in sequence.
    Stops early if no tasks are available or if processing fails,
    allowing the worker to sleep and try again later.
    """
    # while we are finding tasks to claim, keep working for a bit but not forever
    for _ in range(20):
        if hctx.shutdown.is_set():
            # soft shutdown in progress: leave remaining tasks for other instances
            return
        task = await claim_event(
            pool=hctx.pool,
            max_attempts=max_attempts,
            invisibility_minutes=invisibility_minutes,
        )
        if not task:
            return
        if not await process_task(task_processor, hctx, task):
            # if we failed to process the task, stop working for now
            return
