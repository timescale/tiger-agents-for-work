"""Helpers for soft (graceful) shutdown.

A soft shutdown is requested by setting ``HarnessContext.shutdown``. Workers
finish the task they are processing and stop claiming new ones, the Slack
listener disconnects so Slack routes events elsewhere, and loops that never
exit on their own are cancelled via :func:`cancel_on_shutdown`.
"""

import asyncio
from collections.abc import Coroutine
from contextlib import suppress
from typing import Any


async def cancel_on_shutdown(
    shutdown: asyncio.Event, coro: Coroutine[Any, Any, Any]
) -> None:
    """Run ``coro`` until it finishes or ``shutdown`` is set, whichever is first.

    Intended for long-running loops (streaming subscriptions, schedulers) that
    have no natural exit, so a soft shutdown can stop them without each loop
    having to poll the shutdown flag itself.
    """
    task = asyncio.ensure_future(coro)
    waiter = asyncio.ensure_future(shutdown.wait())
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
