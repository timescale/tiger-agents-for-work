import logging

import logfire

from tiger_agent.db.utils import claim_event, delete_event
from tiger_agent.events.types import Event, EventProcessor, HarnessContext

logger = logging.getLogger(__name__)


async def process_event(
    event_processor: EventProcessor, hctx: HarnessContext, event: Event
) -> bool:
    """Process a single claimed event.

    Calls the registered event processor with the event and harness context.
    On success, marks the event as completed. On failure, leaves the event
    in the queue for retry by other workers.

    Args:
        event: The claimed event to process

    Returns:
        bool: True if processing succeeded, False if it failed
    """
    with logfire.span("process_event", event=event) as _:
        try:
            await event_processor(hctx, event)
            await delete_event(pool=hctx.pool, event=event)
            return True
        except Exception as e:
            logger.exception(
                "event processing failed", extra={"event_id": event.id}, exc_info=e
            )
            # Event remains in database for retry
        return False


async def process_events(
    event_processor: EventProcessor,
    hctx: HarnessContext,
    max_attempts: int,
    invisibility_minutes: int,
):
    """Process available events in a batch.

    Attempts to claim and process up to 20 events in sequence.
    Stops early if no events are available or if processing fails,
    allowing the worker to sleep and try again later.
    """
    # while we are finding events to claim, keep working for a bit but not forever
    for _ in range(20):
        event = await claim_event(
            pool=hctx.pool,
            max_attempts=max_attempts,
            invisibility_minutes=invisibility_minutes,
        )
        if not event:
            return
        if not await process_event(event_processor, hctx, event):
            # if we failed to process the event, stop working for now
            return
