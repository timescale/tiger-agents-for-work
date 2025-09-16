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
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from slack_bolt.adapter.socket_mode.websockets import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck

from tiger_agent.migrations import runner

logger = logging.getLogger(__name__)


async def configure_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


async def reset_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


def _create_default_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        check=AsyncConnectionPool.check_connection,
        configure=configure_database_connection,
        reset=reset_database_connection,
    )


def _create_default_app() -> AsyncApp:
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    assert slack_bot_token is not None, (
        "SLACK_BOT_TOKEN environment variable is missing!"
    )

    return AsyncApp(
        token=slack_bot_token,
        ignoring_self_events_enabled=False,
    )


@dataclass
class EventContext:
    app: AsyncApp
    pool: AsyncConnectionPool
    task_group: TaskGroup
    bot_id: str
    bot_name: str
    team_id: str
    team_name: str
    app_id: str


@dataclass
class Event:
    id: int
    event_ts: datetime
    attempts: int
    vt: datetime
    event: dict[str, Any]


# Type alias for event processing callback
EventProcessor = Callable[[EventContext, Event], Awaitable[None]]


class AgentHarness:
    def __init__(
        self,
        event_processor: EventProcessor,
        app: AsyncApp | None = None,
        pool: AsyncConnectionPool | None = None,
        worker_sleep_seconds: int = 60,
        worker_min_jitter_seconds: int = -15,
        worker_max_jitter_seconds: int = 15,
    ):
        self._task_group: TaskGroup | None = None
        self.app = app if app is not None else _create_default_app()
        self.pool = pool if pool is not None else _create_default_pool()
        self._trigger = asyncio.Queue()
        self._event_processor = event_processor
        self._worker_sleep_seconds = worker_sleep_seconds
        self._worker_min_jitter_seconds = worker_min_jitter_seconds
        self._worker_max_jitter_seconds = worker_max_jitter_seconds
        assert worker_sleep_seconds > 0
        assert worker_sleep_seconds - worker_min_jitter_seconds > 0
        assert worker_max_jitter_seconds > worker_min_jitter_seconds

    @logfire.instrument("insert_event", extract_args=False)
    async def _insert_event(self, event: dict[str, Any]) -> None:
        async with (
            self.pool.connection() as con,
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
            self.pool.connection() as con,
            con.transaction() as _,
            con.cursor(row_factory=class_row(Event)) as cur,
        ):
            await cur.execute("select * from agent.claim_event()")
            result = await cur.fetchone()
            return result

    @logfire.instrument("delete_event", extract_args=False)
    async def _delete_event(self, event: Event) -> None:
        async with (
            self.pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute("select agent.delete_event(%s)", (event.id,))

    @logfire.instrument("delete_expired_events", extract_args=False)
    async def _delete_expired_events(self) -> None:
        async with (
            self.pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute("select agent.delete_expired_events()")

    def _make_event_context(self) -> EventContext:
        return EventContext(
            self.app,
            self.pool,
            self._task_group,
            self._bot_id,
            self._bot_name,
            self._team_id,
            self._team_name,
            self._app_id,
        )

    async def _process_event(self, event: Event):
        with logfire.span("process_event", event_id=event.id) as _:
            try:
                await self._event_processor(self._make_event_context(), event)
                await self._delete_event(event)
            except Exception as e:
                logger.exception(
                    "event processing failed", extra={"event_id": event.id}, exc_info=e
                )
                # Event remains in database for retry

    @logfire.instrument("process_events", extract_args=False)
    async def _process_events(self):
        while True:
            event = await self._claim_event()
            if not event:
                logger.info("no event found")
                return
            await self._process_event(event)

    async def _worker(self, worker_id: int):
        async def worker_run(reason: str):
            with logfire.span("worker_run", worker_id=worker_id, reason=reason) as _:
                await self._process_events()
                await self._delete_expired_events()

        # initial staggering of workers
        await asyncio.sleep(random.randint(0, self._worker_sleep_seconds))

        while True:
            try:
                jitter = random.randint(
                    self._worker_min_jitter_seconds, self._worker_max_jitter_seconds
                )
                await asyncio.wait_for(
                    self._trigger.get(), timeout=(self._worker_sleep_seconds + jitter)
                )
                await worker_run("triggered")
            except TimeoutError:
                await worker_run("timeout")
            except QueueShutDown:
                return

    @logfire.instrument("fetch_bot_info", extract_args=False)
    async def _fetch_bot_info(self):
        resp = await self.app.client.auth_test()
        assert isinstance(resp.data, dict), "resp.data must be a dict"
        assert resp.data["ok"] == True, "slack auth_test failed"
        self._bot_id: str = resp.data["bot_id"]
        self._team_id: str = resp.data["team_id"]
        self._team_name: str = resp.data["team"]
        resp = await self.app.client.bots_info(bot=self._bot_id, team_id=self._team_id)
        assert isinstance(resp.data, dict), "resp.data must be a dict"
        assert resp.data["ok"] == True, "slack bot_info failed"
        bot = resp.data["bot"]
        self._bot_name: str = bot["name"]
        self._app_id: str = bot["app_id"]

    async def run(self, app_token: str, task_group: TaskGroup, num_workers: int = 5):
        await self.pool.wait()
        self._task_group = task_group

        await self._fetch_bot_info()

        async with self.pool.connection() as con:
            await runner.migrate_db(con)

        async def on_event(ack: AsyncAck, event: dict[str, Any]):
            await self._on_event(ack, event)

        for worker_id in range(num_workers):
            logger.info("creating worker", extra={"worker_id": worker_id})
            task_group.create_task(self._worker(worker_id))

        self.app.event("app_mention")(on_event)

        handler = AsyncSocketModeHandler(self.app, app_token)
        task_group.create_task(handler.start_async())
