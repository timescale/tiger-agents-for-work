import asyncio
import random
from asyncio import TaskGroup, QueueShutDown
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

import logfire
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck

from tiger_agent.migrations import runner


@dataclass
class Event:
    id: int
    event_ts: datetime
    attempts: int
    vt: datetime
    event: dict[str, Any]

# Type alias for event processing callback
EventProcessor = Callable[[Event], Awaitable[None]]


class AgentHarness:
    def __init__(self, app: AsyncApp, pool: AsyncConnectionPool, event_processor: EventProcessor):
        self.app = app
        self.pool = pool
        self._trigger = asyncio.Queue()
        self._event_processor = event_processor

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

    async def _process_event(self, event: Event):
        with logfire.span("process_event", event_id=event.id) as _:
            try:
                await self._event_processor(event)
                await self._delete_event(event)
            except Exception as e:
                logfire.exception("event processing failed", event_id=event.id, error=e)
                # Event remains in database for retry

    @logfire.instrument("process_events", extract_args=False)
    async def _process_events(self):
        while True:
            event = await self._claim_event()
            if not event:
                logfire.info("no event found")
                return
            await self._process_event(event)

    async def _worker(self, worker_id: int):
        async def worker_run(reason: str):
            with logfire.span("worker_run", worker_id=worker_id, reason=reason) as _:
                await self._process_events()
                await self._delete_expired_events()
        
        # initial staggering of workers
        await asyncio.sleep(random.randint(0, 30))
        
        while True:
            try:
                jitter = random.randint(-15, 15)
                await asyncio.wait_for(self._trigger.get(), timeout=(60.0 + jitter))
                await worker_run("triggered")
            except TimeoutError:
                await worker_run("timeout")
            except QueueShutDown:
                return

    async def run(self, task_group: TaskGroup, num_workers: int = 5):
        async with self.pool.connection() as con:
            await runner.migrate_db(con)
        
        async def on_event(ack: AsyncAck, event: dict[str, Any]):
            await self._on_event(ack, event)
        
        for worker_id in range(num_workers):
            logfire.info("creating worker", worker_id=worker_id)
            task_group.create_task(self._worker(worker_id))

        self.app.event("app_mention")(on_event)
