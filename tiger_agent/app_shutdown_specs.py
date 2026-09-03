"""Signal handling in TigerApp.run(): soft shutdown, grace deadline, forced stop.

TigerApp is built without its constructor so no Slack app, agent, or database
is needed; the worker/listener harnesses are replaced with stubs that put a
single task into the TaskGroup.
"""

import asyncio
import os
import signal
from asyncio import Queue, TaskGroup
from collections.abc import Callable, Coroutine
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.app import TigerApp
from tiger_agent.types import HarnessContext


class _StubTaskHarness:
    def __init__(self, work: Callable[[], Coroutine]):
        self._work = work

    async def run(self, tasks: TaskGroup):
        tasks.create_task(self._work())


class _StubListenerHarness:
    async def start(self, tasks: TaskGroup):
        pass


@pytest.fixture
def hctx(make_bot_info) -> HarnessContext:
    return HarnessContext(
        app=MagicMock(),
        pool=AsyncMock(),
        trigger=Queue(),
        bot_info=make_bot_info(),
        num_workers=1,
    )


def _make_app(hctx: HarnessContext, work, grace_seconds: float) -> TigerApp:
    app = TigerApp.__new__(TigerApp)
    app._hctx = hctx
    app._task_harness = _StubTaskHarness(work)
    app._listener_harness = _StubListenerHarness()
    app._shutdown_grace_seconds = grace_seconds
    app._main_task = None
    app._deadline = None
    app._forced = False
    return app


async def _start(app: TigerApp) -> asyncio.Task:
    runner = asyncio.create_task(app.run())
    await asyncio.sleep(0.01)  # let run() install signal handlers
    return runner


async def test_sigterm_lets_in_flight_work_finish(hctx):
    finished = asyncio.Event()

    async def work():
        await hctx.shutdown.wait()
        await asyncio.sleep(0.05)  # wrapping up after noticing shutdown
        finished.set()

    app = _make_app(hctx, work, grace_seconds=5)
    runner = await _start(app)

    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(runner, timeout=2)

    assert finished.is_set()
    assert not app._forced
    assert hctx.trigger.qsize() == hctx.num_workers, "idle workers are woken"
    hctx.pool.close.assert_awaited_once()


async def test_grace_deadline_cancels_in_flight_work(hctx):
    cancelled = asyncio.Event()

    async def work():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    app = _make_app(hctx, work, grace_seconds=0.05)
    runner = await _start(app)

    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(runner, timeout=2)

    assert cancelled.is_set()
    assert app._forced
    hctx.pool.close.assert_awaited_once()


async def test_second_signal_forces_shutdown(hctx):
    async def work():
        await asyncio.sleep(3600)

    app = _make_app(hctx, work, grace_seconds=3600)
    runner = await _start(app)

    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(0.01)
    assert hctx.shutdown.is_set()
    assert not runner.done()

    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(runner, timeout=2)
    assert app._forced
