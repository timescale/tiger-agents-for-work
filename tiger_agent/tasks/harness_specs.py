import asyncio
from asyncio import Queue
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.tasks import harness as harness_module
from tiger_agent.tasks import utils as task_utils
from tiger_agent.tasks.harness import TaskHarness
from tiger_agent.types import HarnessContext


@pytest.fixture
def hctx(make_bot_info) -> HarnessContext:
    return HarnessContext(
        app=MagicMock(),
        pool=MagicMock(),
        trigger=Queue(),
        bot_info=make_bot_info(),
        num_workers=1,
    )


@pytest.fixture
def worker_deps(monkeypatch):
    """Stub the DB-touching calls a worker run makes."""
    process_tasks = AsyncMock()
    delete_expired = AsyncMock()
    monkeypatch.setattr(harness_module, "process_tasks", process_tasks)
    monkeypatch.setattr(harness_module, "delete_expired_events", delete_expired)
    return process_tasks, delete_expired


def _start_worker(hctx: HarnessContext, initial_sleep: int = 0) -> asyncio.Task:
    harness = TaskHarness(MagicMock(), hctx=hctx)
    return asyncio.create_task(harness._worker(0, initial_sleep))


async def test_idle_worker_exits_when_woken_during_shutdown(hctx, worker_deps):
    process_tasks, _ = worker_deps
    worker = _start_worker(hctx)
    await asyncio.sleep(0)  # worker is now blocked on the trigger

    hctx.shutdown.set()
    hctx.trigger.put_nowait(None)

    await asyncio.wait_for(worker, timeout=1)
    process_tasks.assert_not_awaited()


async def test_worker_finishes_in_flight_run_before_exiting(hctx, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_process_tasks(*_args, **_kwargs):
        started.set()
        await release.wait()

    delete_expired = AsyncMock()
    monkeypatch.setattr(harness_module, "process_tasks", slow_process_tasks)
    monkeypatch.setattr(harness_module, "delete_expired_events", delete_expired)

    worker = _start_worker(hctx)
    hctx.trigger.put_nowait(True)
    await asyncio.wait_for(started.wait(), timeout=1)

    hctx.shutdown.set()
    hctx.trigger.put_nowait(None)
    await asyncio.sleep(0.05)
    assert not worker.done(), "worker must not abandon its in-flight run"

    release.set()
    await asyncio.wait_for(worker, timeout=1)
    delete_expired.assert_awaited_once()


async def test_worker_in_initial_sleep_exits_on_shutdown(hctx, worker_deps):
    process_tasks, _ = worker_deps
    worker = _start_worker(hctx, initial_sleep=600)
    await asyncio.sleep(0)

    hctx.shutdown.set()

    await asyncio.wait_for(worker, timeout=1)
    process_tasks.assert_not_awaited()


async def test_process_tasks_stops_claiming_during_shutdown(hctx, monkeypatch):
    claim_event = AsyncMock()
    monkeypatch.setattr(task_utils, "claim_event", claim_event)
    hctx.shutdown.set()

    await task_utils.process_tasks(
        MagicMock(), hctx, max_attempts=3, invisibility_minutes=10
    )

    claim_event.assert_not_awaited()
