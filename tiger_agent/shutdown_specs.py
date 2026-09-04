import asyncio

import pytest

from tiger_agent.shutdown import cancel_on_shutdown


async def test_cancels_coroutine_when_shutdown_is_set():
    shutdown = asyncio.Event()
    cancelled = asyncio.Event()

    async def forever():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runner = asyncio.create_task(cancel_on_shutdown(shutdown, forever()))
    await asyncio.sleep(0)
    shutdown.set()

    await asyncio.wait_for(runner, timeout=1)
    assert cancelled.is_set()


async def test_returns_when_coroutine_finishes_first():
    shutdown = asyncio.Event()

    async def quick():
        return "done"

    await asyncio.wait_for(cancel_on_shutdown(shutdown, quick()), timeout=1)
    assert not shutdown.is_set()


async def test_propagates_coroutine_errors():
    async def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await cancel_on_shutdown(asyncio.Event(), boom())
