"""
Tiger Agent Event Processing Harness.

This module provides the core event processing infrastructure for Tiger Agent,
implementing a durable work queue system using PostgreSQL with TimescaleDB.
The harness manages Slack app_mention events through a multi-worker architecture
with atomic event claiming, retry logic, and automatic cleanup.

Key Components:
- EventHarness: Main orchestrator for event processing
- HarnessContext: Shared resources (Slack app, database pool, task group) for event processors
- Event/AppMentionEvent: Data models for Slack events
- Database integration with agent.event table as work queue
"""

import asyncio
import logging
import os
import random
import re
from asyncio import QueueShutDown, TaskGroup
from collections.abc import Awaitable, Callable
from typing import Any

import logfire
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError
from slack_bolt.adapter.socket_mode.websockets import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.respond.async_respond import AsyncRespond

from tiger_agent.commands import handle_command
from tiger_agent.migrations import runner
from tiger_agent.slack import fetch_bot_info
from tiger_agent.types import Event, HarnessContext, SlackCommand

logger = logging.getLogger(__name__)

pg_max_pool_size: int = int(os.getenv("PG_MAX_POOL_SIZE", "10"))

async def _configure_database_connection(con: AsyncConnection) -> None:
    """Configure new database connections with autocommit enabled."""
    await con.set_autocommit(True)


async def _reset_database_connection(con: AsyncConnection) -> None:
    """Reset database connections to autocommit mode when returned to pool."""
    await con.set_autocommit(True)


def _create_default_pool(min_size: int, max_size: int) -> AsyncConnectionPool:
    """Create a default PostgreSQL connection pool with standard configuration.

    Returns:
        AsyncConnectionPool: Configured pool with autocommit and connection lifecycle handlers.
    """
    return AsyncConnectionPool(
        check=AsyncConnectionPool.check_connection,
        configure=_configure_database_connection,
        min_size=min_size,
        max_size=max_size,
        open=False,
        reset=_reset_database_connection,
    )



# Type alias for event processing callback
EventProcessor = Callable[[HarnessContext, Event], Awaitable[None]]


class EventHarness:
    """
    Core event processing harness for Tiger Agent with bounded concurrency and immediate responsiveness.

    The EventHarness orchestrates the entire event processing pipeline:
    1. Receives Slack app_mention events via Socket Mode
    2. Stores events durably in PostgreSQL (agent.event table)
    3. Coordinates multiple workers to claim and process events
    4. Handles retries, timeouts, and cleanup of expired events

    Key architectural features:

    **Bounded Concurrency**: Fixed number of worker tasks (num_workers) ensures predictable
    resource usage and prevents overwhelming downstream systems.

    **Immediate Event Handling**: When events arrive, exactly one worker is immediately "poked" via an
    asyncio.Queue trigger, ensuring events are processed without delay rather than
    waiting for the next polling cycle.

    **Atomic Event Claiming**: Multiple workers compete for events using agent.claim_event(),
    which atomically assigns events to exactly one worker, preventing duplicate processing.

    **Resilient Retry Logic**: Failed/missed events are automatically retried through:
    - Periodic worker polling (with jitter to prevent thundering herd)
    - Automatic cleanup of expired/stuck events
    - Visibility thresholds that make failed events available for retry

    The harness implements a work queue pattern where:
    - Events are atomically claimed by workers using agent.claim_event()
    - Failed processing leaves events available for retry
    - Successful processing moves events to agent.event_hist
    - Expired events are automatically cleaned up

    Args:
        event_processor: Callback function that processes claimed events
        app: Optional Slack AsyncApp (creates default if None)
        pool: Optional database connection pool (creates default if None)
        worker_sleep_seconds: Base sleep time between worker runs
        worker_min_jitter_seconds: Minimum random jitter for worker sleep
        worker_max_jitter_seconds: Maximum random jitter for worker sleep
        max_attempts: Maximum retry attempts per event
        max_age_minutes: Maximum age before events are expired
        invisibility_minutes: How long claimed events remain invisible
        num_workers: Number of concurrent worker tasks (bounded concurrency)
        slack_bot_token: Slack bot token (uses SLACK_BOT_TOKEN env if None)
        slack_app_token: Slack app token (uses SLACK_APP_TOKEN env if None)
    """
    def __init__(
        self,
        event_processor: EventProcessor,
        app: AsyncApp | None = None,
        pool: AsyncConnectionPool | None = None,
        worker_sleep_seconds: int = 60,
        worker_min_jitter_seconds: int = -15,
        worker_max_jitter_seconds: int = 15,
        max_attempts: int = 3,
        max_age_minutes: int = 60,
        invisibility_minutes: int = 10,
        num_workers: int = 5,
        slack_bot_token: str | None = None,
        slack_app_token: str | None = None,
    ):
        self._task_group: TaskGroup | None = None
        self._pool = pool if pool is not None else _create_default_pool(num_workers + 1, pg_max_pool_size)
        self._trigger = asyncio.Queue()
        self._event_processor = event_processor
        self._worker_sleep_seconds = worker_sleep_seconds
        self._worker_min_jitter_seconds = worker_min_jitter_seconds
        self._worker_max_jitter_seconds = worker_max_jitter_seconds
        self._max_attempts = max_attempts
        self._max_age_minutes = max_age_minutes
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
        """Insert a Slack event into the database work queue.

        Uses the agent.insert_event() database function to store the event
        with proper timestamp conversion and initial queue state.

        Args:
            event: Raw Slack event payload as dictionary
        """
        async with (
            self._pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute("select agent.insert_event(%s)", (Jsonb(event),))

    @logfire.instrument("on_event", extract_args=False)
    async def _on_event(self, ack: AsyncAck, event: dict[str, Any]):
        """Handle incoming Slack app_mention events with immediate worker notification.

        This method implements the "poke" mechanism for immediate event processing:
        1. Stores the event durably in the database
        2. Acknowledges to Slack to prevent retries
        3. "Pokes" exactly one worker via asyncio.Queue trigger for immediate processing

        The trigger ensures that one worker wakes up immediately to process available events
        rather than waiting for the next polling cycle. This provides excellent responsiveness
        while maintaining the resilience of periodic polling for retries and avoiding
        thundering herd effects.

        Args:
            ack: Slack acknowledgment callback
            event: Raw Slack event payload
        """
        await self._insert_event(event)
        await ack()
        await self._trigger.put(True)

    @logfire.instrument("claim_event", extract_args=False)
    async def _claim_event(self) -> Event | None:
        """Atomically claim an event for processing.

        Uses agent.claim_event() to find and lock an available event,
        updating its visibility threshold to prevent other workers from
        claiming it simultaneously.

        Returns:
            Event: Claimed event ready for processing, or None if no events available
        """
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
            try:
                assert row["id"] is not None, "claimed an empty event"
                return Event(**row)
            except ValidationError as e:
                logger.exception("failed to parse claimed event", exc_info=e, extra={"id": row.get("id")})
                if row["id"] is not None:
                    # if we got a malformed event, delete it to avoid retry loops
                    await cur.execute("select agent.delete_event(%s::int8, _processed=>false)", (row["id"],))
                return None

    @logfire.instrument("delete_event", extract_args=False)
    async def _delete_event(self, event: Event) -> None:
        """Mark an event as successfully processed.

        Uses agent.delete_event() to atomically move the event from
        agent.event to agent.event_hist, indicating successful processing.

        Args:
            event: The event that was successfully processed
        """
        async with (
            self._pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute("select agent.delete_event(%s)", (event.id,))

    @logfire.instrument("delete_expired_events", extract_args=False)
    async def _delete_expired_events(self) -> None:
        """Clean up events that have exceeded retry limits or are too old.

        Uses agent.delete_expired_events() to move events that have been
        attempted too many times or are stuck invisible for too long to
        the history table.
        """
        async with (
            self._pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute(
                "select agent.delete_expired_events(%s, %s::int8 * interval '1m')",
                (self._max_attempts, self._max_age_minutes)
            )

    def _make_harness_context(self) -> HarnessContext:
        """Create a context object for event processors.

        Returns:
            HarnessContext: Context containing Slack app, database pool, and task group
        """
        return HarnessContext(
            self._app,
            self._pool,
        )

    async def _process_event(self, event: Event) -> bool:
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
        """Process available events in a batch.

        Attempts to claim and process up to 20 events in sequence.
        Stops early if no events are available or if processing fails,
        allowing the worker to sleep and try again later.
        """
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
        """Calculate sleep duration for worker with random jitter.

        Adds random jitter to the base sleep time to prevent workers
        from synchronizing and creating thundering herd effects.

        Returns:
            int: Sleep duration in seconds with jitter applied
        """
        jitter = random.randint(
            self._worker_min_jitter_seconds, self._worker_max_jitter_seconds
        )
        return self._worker_sleep_seconds + jitter

    async def _worker(self, worker_id: int, initial_sleep_seconds: int):
        """Main worker loop for processing events.

        Each worker runs independently, either triggered by new events
        or by timeout. Workers are initially staggered to distribute
        load and prevent thundering herd effects.

        Args:
            worker_id: Unique identifier for this worker
            initial_sleep_seconds: Initial delay before starting work
        """
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
        """Generate worker arguments with staggered start times.

        Creates a list of (worker_id, initial_sleep) tuples where the first
        worker starts immediately and subsequent workers are randomly staggered.

        Args:
            num_workers: Number of workers to create

        Returns:
            list[tuple[int, int]]: List of (worker_id, initial_sleep_seconds) pairs
        """
        initial_sleeps: list[int] = [0]  # first worker starts immediately
        # pick num_workers - 1 unique initial sleep values
        initial_sleeps.extend(random.sample(range(1, self._worker_sleep_seconds), num_workers - 1))
        return [(worker_id, initial_sleep) for worker_id, initial_sleep in enumerate(initial_sleeps)]

    async def run(self):
        """Start the event harness and run indefinitely.

        This method:
        1. Opens the database connection pool
        2. Runs database migrations
        3. Creates and starts worker tasks
        4. Sets up Slack event handling
        5. Starts the Slack Socket Mode connection

        All tasks run concurrently in a TaskGroup. The method blocks
        until interrupted or an unhandled exception occurs.
        """
        await self._pool.open(wait=True)

        bot_info = await fetch_bot_info(self._app.client)

        async with asyncio.TaskGroup() as tasks:
            async with self._pool.connection() as con:
                await runner.migrate_db(con)

            logger.info(f"creating {self._num_workers} workers")
            for worker_id, initial_sleep in self._worker_args(self._num_workers):
                logger.info("creating worker", extra={"worker_id": worker_id})
                tasks.create_task(self._worker(worker_id, initial_sleep))

            async def on_event(ack: AsyncAck, event: dict[str, Any]):
                await self._on_event(ack, event)
                
            async def on_command(ack: AsyncAck, respond: AsyncRespond, command: SlackCommand):
                await ack()
                response = await handle_command(command=command, hctx=self._make_harness_context())
                await respond(text=response, response_type="ephemeral", delete_original=True)
                

            self._app.command(re.compile(r"\/.*"))(on_command)
            self._app.event("app_mention")(on_event)

            async def on_message(ack: AsyncAck, event: dict[str, Any]):
                if event["user"] == bot_info.user_id:
                    await ack()
                    return
                event["subtype"] = event["channel_type"]
                # We don't also handle message.mpim events here, since we only
                # respond there if the bot is explicitly mentioned, which will
                # trigger the app_mention handler above.
                if event["subtype"] not in ("im"):
                    await ack()
                    return
                await self._on_event(ack, event)
            self._app.event("message")(on_message)

            handler = AsyncSocketModeHandler(self._app, app_token=self._slack_app_token)
            tasks.create_task(handler.start_async())

