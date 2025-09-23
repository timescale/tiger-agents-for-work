import asyncio
import logging
import os
import random
from asyncio import QueueShutDown, TaskGroup
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import logfire
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from slack_bolt.adapter.socket_mode.websockets import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck

from tiger_agent.migrations import runner

logger = logging.getLogger(__name__)


async def _configure_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


async def _reset_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


def _create_default_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        check=AsyncConnectionPool.check_connection,
        configure=_configure_database_connection,
        reset=_reset_database_connection,
        open=False,
    )


@dataclass
class HarnessContext:
    app: AsyncApp
    pool: AsyncConnectionPool
    task_group: TaskGroup


class AppMentionEvent(BaseModel):
    model_config = {"extra": "allow"}

    ts: str
    thread_ts: str | None = None
    team: str
    text: str
    type: str
    user: str
    blocks: list[dict[str, Any]] | None = None
    channel: str
    event_ts: str
    client_msg_id: str


class Event(BaseModel):
    id: int
    event_ts: datetime
    attempts: int
    vt: datetime
    claimed: list[datetime]
    event: AppMentionEvent


# Type alias for event processing callback
EventProcessor = Callable[[HarnessContext, Event], Awaitable[None]]


class EventHarness:
    def __init__(
        self,
        event_processor: EventProcessor,
        app: AsyncApp | None = None,
        pool: AsyncConnectionPool | None = None,
        worker_sleep_seconds: int = 60,
        worker_min_jitter_seconds: int = -15,
        worker_max_jitter_seconds: int = 15,
        max_attempts: int = 3,
        invisibility_minutes: int = 10,
        num_workers: int = 5,
        slack_bot_token: str | None = None,
        slack_app_token: str | None = None,
    ):
        self._task_group: TaskGroup | None = None
        self._pool = pool if pool is not None else _create_default_pool()
        self._trigger = asyncio.Queue()
        self._event_processor = event_processor
        self._worker_sleep_seconds = worker_sleep_seconds
        self._worker_min_jitter_seconds = worker_min_jitter_seconds
        self._worker_max_jitter_seconds = worker_max_jitter_seconds
        self._max_attempts = max_attempts
        self._num_workers = num_workers
        self._invisibility_minutes = invisibility_minutes
        self._slack_bot_token = slack_bot_token or os.getenv("SLACK_BOT_TOKEN")
        assert self._slack_bot_token is not None, "no SLACK_BOT_TOKEN found"
        self._slack_app_token = slack_app_token or os.getenv("SLACK_APP_TOKEN")
        assert self._slack_app_token is not None, "no SLACK_APP_TOKEN found"
        assert worker_sleep_seconds > 0
        assert worker_sleep_seconds - worker_min_jitter_seconds > 0
        assert worker_max_jitter_seconds > worker_min_jitter_seconds
        self._app = app if app is not None else AsyncApp(
            token=self._slack_bot_token,
            ignoring_self_events_enabled=False,
        )

    @logfire.instrument("insert_event", extract_args=False)
    async def _insert_event(self, event: dict[str, Any]) -> None:
        async with (
            self._pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute("select agent.insert_event(%s)", (Jsonb(event),))

    @logfire.instrument("on_event", extract_args=False)
    async def _on_event(self, ack: AsyncAck, event: dict[str, Any]):
        await self._insert_event(event)
        await ack()
        await self._trigger.put(True)

    @logfire.instrument("claim_event", extract_args=False)
    async def _claim_event(self) -> Event | None:
        async with (
            self._pool.connection() as con,
            con.transaction() as _,
            con.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "select * from agent.claim_event(%s, %s::int8 * interval '1m')",
                (self._max_attempts, self._invisibility_minutes)
            )
            row: dict[str, Any] | None = await cur.fetchone()
            if not row:
                return None
            assert row["id"] is not None, "claimed an empty event"
            return Event(**row)

    @logfire.instrument("delete_event", extract_args=False)
    async def _delete_event(self, event: Event) -> None:
        async with (
            self._pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute("select agent.delete_event(%s)", (event.id,))

    @logfire.instrument("delete_expired_events", extract_args=False)
    async def _delete_expired_events(self) -> None:
        async with (
            self._pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute(
                "select agent.delete_expired_events(%s, %s::int8 * interval '1m')",
                (self._max_attempts, self._invisibility_minutes)
            )

    def _make_harness_context(self) -> HarnessContext:
        return HarnessContext(
            self._app,
            self._pool,
            self._task_group,
        )

    async def _process_event(self, event: Event) -> bool:
        with logfire.span("process_event", event_id=event.id) as _:
            try:
                await self._event_processor(self._make_harness_context(), event)
                await self._delete_event(event)
                return True
            except Exception as e:
                logger.exception(
                    "event processing failed", extra={"event_id": event.id}, exc_info=e
                )
                # Event remains in database for retry
            return False

    @logfire.instrument("process_events", extract_args=False)
    async def _process_events(self):
        # while we are finding events to claim, keep working for a bit but not forever
        for _ in range(20):
            event = await self._claim_event()
            if not event:
                logger.info("no event found")
                return
            if not await self._process_event(event):
                # if we failed to process the event, stop working for now
                return

    def _calc_worker_sleep(self) -> int:
        jitter = random.randint(
            self._worker_min_jitter_seconds, self._worker_max_jitter_seconds
        )
        return self._worker_sleep_seconds + jitter

    async def _worker(self, worker_id: int, initial_sleep_seconds: int):
        async def worker_run(reason: str):
            with logfire.span("worker_run", worker_id=worker_id, reason=reason) as _:
                await self._process_events()
                await self._delete_expired_events()

        # initial staggering of workers
        if initial_sleep_seconds > 0:
            logger.info("worker initial sleep", extra={"worker_id": worker_id, "initial_sleep_seconds": initial_sleep_seconds})
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
        initial_sleeps: list[int] = [0]  # first worker starts immediately
        # pick num_workers - 1 unique initial sleep values
        initial_sleeps.extend(random.sample(range(1, self._worker_sleep_seconds), num_workers - 1))
        return [(worker_id, initial_sleep) for worker_id, initial_sleep in enumerate(initial_sleeps)]

    async def run(self):
        await self._pool.open(wait=True)

        async with asyncio.TaskGroup() as tasks:
            async with self._pool.connection() as con:
                await runner.migrate_db(con)

            async def on_event(ack: AsyncAck, event: dict[str, Any]):
                await self._on_event(ack, event)

            logger.info(f"creating {self._num_workers} workers")
            for worker_id, initial_sleep in self._worker_args(self._num_workers):
                logger.info("creating worker", extra={"worker_id": worker_id})
                tasks.create_task(self._worker(worker_id, initial_sleep))

            self._app.event("app_mention")(on_event)

            handler = AsyncSocketModeHandler(self._app, app_token=self._slack_app_token)
            tasks.create_task(handler.start_async())

