import logging

import logfire

from tiger_agent.db.utils import claim_event, delete_event
from tiger_agent.tasks.types import Task, TaskProcessor
from tiger_agent.types import Context

logger = logging.getLogger(__name__)


async def process_task(
    task_processor: TaskProcessor, ctx: Context, task: Task
) -> bool:
    """Process a single claimed task.

    Calls the registered task processor with the task and context.
    On success, marks the task as completed. On failure, leaves the task
    in the queue for retry by other workers.

    Args:
        task: The claimed task to process

    Returns:
        bool: True if processing succeeded, False if it failed
    """
    with logfire.span("process_task", task=task) as _:
        try:
            await task_processor(ctx, task)
            await delete_event(pool=ctx.pool, event=task)
            return True
        except Exception as e:
            logger.exception(
                "task processing failed", extra={"task_id": task.id}, exc_info=e
            )
            # Task remains in database for retry
        return False


async def process_tasks(
    task_processor: TaskProcessor,
    ctx: Context,
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
        task = await claim_event(
            pool=ctx.pool,
            max_attempts=max_attempts,
            invisibility_minutes=invisibility_minutes,
        )
        if not task:
            return
        if not await process_task(task_processor, ctx, task):
            # if we failed to process the task, stop working for now
            return
