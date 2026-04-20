import logging
from datetime import timedelta
from typing import Any

import logfire
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from tiger_agent.db.constants import PG_MAX_POOL_SIZE
from tiger_agent.events.types import Event

logger = logging.getLogger(__name__)


async def _configure_database_connection(con: AsyncConnection) -> None:
    """Configure new database connections with autocommit enabled."""
    await con.set_autocommit(True)


async def _reset_database_connection(con: AsyncConnection) -> None:
    """Reset database connections to autocommit mode when returned to pool."""
    await con.set_autocommit(True)


def create_default_pool(num_workers: int) -> AsyncConnectionPool:
    """Create a default PostgreSQL connection pool with standard configuration.

    Returns:
        AsyncConnectionPool: Configured pool with autocommit and connection lifecycle handlers.
    """
    return AsyncConnectionPool(
        check=AsyncConnectionPool.check_connection,
        configure=_configure_database_connection,
        min_size=num_workers + 1,
        max_size=PG_MAX_POOL_SIZE,
        open=False,
        reset=_reset_database_connection,
    )


async def usage_limit_reached(
    pool: AsyncConnectionPool,
    user_id: str,
    interval: timedelta,
    allowed_requests: int | None,
) -> bool:
    """Determine if the user's request should be processed."""
    if allowed_requests is None:
        return False

    async with pool.connection() as con:
        """Count user requests in both agent.event and agent.event_hist tables within the given interval."""
        result = await con.execute(
            """SELECT COUNT(*) FROM (
                SELECT 1 FROM agent.event
                WHERE event->>'user' = %s AND event_ts >= now() - %s
                UNION ALL
                SELECT 1 FROM agent.event_hist
                WHERE event->>'user' = %s AND event_ts >= now() - %s
            ) combined""",
            (user_id, interval, user_id, interval),
        )
        row = await result.fetchone()
        total_requests = int(row[0]) if row and row[0] is not None else 0
        return total_requests > allowed_requests


async def user_ignored(pool: AsyncConnectionPool, user_id: str) -> bool:
    """Check if a user is currently ignored."""
    async with pool.connection() as con:
        result = await con.execute("SELECT agent.is_user_ignored(%s)", (user_id,))
        row = await result.fetchone()
        return bool(row[0]) if row and row[0] is not None else False


async def user_is_admin(pool: AsyncConnectionPool, user_id: str) -> bool:
    """Check if a user is an admin."""
    async with pool.connection() as con:
        result = await con.execute(
            "SELECT EXISTS(SELECT 1 FROM agent.admin_users WHERE user_id = %s)",
            (user_id,),
        )
        row = await result.fetchone()
        return bool(row[0]) if row and row[0] is not None else False


async def get_salesforce_account_id_for_channel(
    pool: AsyncConnectionPool, channel_id: str
) -> str | None:
    async with pool.connection() as con:
        result = await con.execute(
            "SELECT salesforce_account_id FROM agent.customer_channel_salesforce_link WHERE channel_id = %s",
            (channel_id,),
        )
        row = await result.fetchone()
        return row[0] if row else None


async def upsert_salesforce_account_id_for_channel(
    pool: AsyncConnectionPool, channel_id: str, salesforce_account_id: str
) -> None:
    async with pool.connection() as con:
        await con.execute(
            """INSERT INTO agent.customer_channel_salesforce_link (channel_id, salesforce_account_id)
               VALUES (%s, %s)
               ON CONFLICT (channel_id) DO UPDATE SET salesforce_account_id = EXCLUDED.salesforce_account_id""",
            (channel_id, salesforce_account_id),
        )


async def remove_salesforce_account_id_for_channel(
    pool: AsyncConnectionPool, channel_id: str
) -> None:
    async with pool.connection() as con:
        await con.execute(
            "DELETE FROM agent.customer_channel_salesforce_link WHERE channel_id = %s",
            (channel_id,),
        )


@logfire.instrument("insert_event", extract_args=False)
async def insert_event(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    """Insert a Slack/Salesforce event into the database work queue.

    Uses the agent.insert_event() database function to store the event
    with proper timestamp conversion and initial queue state.

    Args:
        event: Raw Slack/Salesforce event payload as dictionary
    """
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.insert_event(%s)", (Jsonb(event),))


@logfire.instrument("insert_handled_event", extract_args=False)
async def insert_handled_event(pool: AsyncConnectionPool, event: dict[str, Any]) -> int:
    """Insert a Slack event directly into the event history table as processed.

    Uses the agent.insert_event_hist() database function to store the event
    directly in agent.event_hist, bypassing the work queue and marking it as processed.

    Args:
        event: Raw Slack event payload as dictionary

    Returns:
        The ID of the inserted event_hist record
    """
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.insert_event_hist(%s)", (Jsonb(event),))
        result = await cur.fetchone()
        return result[0] if result else None


async def claim_event(
    pool: AsyncConnectionPool, max_attempts: int = 3, invisibility_minutes: int = 10
) -> Event | None:
    """Atomically claim an event for processing.

    Uses agent.claim_event() to find and lock an available event,
    updating its visibility threshold to prevent other workers from
    claiming it simultaneously.

    Returns:
        Event: Claimed event ready for processing, or None if no events available
    """
    with logfire.suppress_instrumentation():
        async with (
            pool.connection() as con,
            con.transaction() as _,
            con.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "select * from agent.claim_event(%s, %s::int8 * interval '1m')",
                (max_attempts, invisibility_minutes),
            )
            row: dict[str, Any] | None = await cur.fetchone()
            if not row:
                return None
            try:
                assert row["id"] is not None, "claimed an empty event"
                return Event(**row)
            except ValidationError as e:
                logger.exception(
                    "failed to parse claimed event",
                    exc_info=e,
                    extra={"id": row.get("id")},
                )
                if row["id"] is not None:
                    # if we got a malformed event, delete it to avoid retry loops
                    await cur.execute(
                        "select agent.delete_event(%s::int8, _processed=>false)",
                        (row["id"],),
                    )
                return None


@logfire.instrument("delete_event", extract_args=False)
async def delete_event(pool: AsyncConnectionPool, event: Event) -> None:
    """Mark an event as successfully processed.

    Uses agent.delete_event() to atomically move the event from
    agent.event to agent.event_hist, indicating successful processing.

    Args:
        event: The event that was successfully processed
    """
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.delete_event(%s)", (event.id,))


@logfire.instrument("get_event_hist", extract_args=False)
async def get_event_hist(pool: AsyncConnectionPool, event_id: int) -> Event | None:
    """Get an event from the event_hist table by ID.

    Retrieves a historical event record and returns it as an Event object.
    Returns None if no event with the given ID is found.

    Args:
        event_id: The ID of the historical event to retrieve

    Returns:
        Event object if found, None otherwise
    """
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute("select * from agent.event_hist where id = %s", (event_id,))
        row: dict[str, Any] | None = await cur.fetchone()
        if not row:
            return None
        try:
            return Event(**row)
        except ValidationError as e:
            logger.exception(
                "failed to parse historical event",
                exc_info=e,
                extra={"id": event_id},
            )
            return None


async def add_salesforce_case_thread(
    pool: AsyncConnectionPool, thread_ts: str, channel_id: str, case_id: str
) -> None:
    async with pool.connection() as con:
        await con.execute(
            """INSERT INTO agent.salesforce_case_thread (channel_id, thread_ts, case_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (channel_id, thread_ts) DO NOTHING""",
            (channel_id, thread_ts, case_id),
        )


async def get_salesforce_case_thread_case_id(
    pool: AsyncConnectionPool, thread_ts: str, channel_id: str
) -> str | None:
    async with pool.connection() as con:
        result = await con.execute(
            "SELECT case_id FROM agent.salesforce_case_thread WHERE channel_id = %s AND thread_ts = %s",
            (channel_id, thread_ts),
        )
        row = await result.fetchone()
        return row[0] if row else None


async def delete_expired_events(
    pool: AsyncConnectionPool, max_attempts: int = 3, max_age_minutes: int = 60
) -> None:
    """Clean up events that have exceeded retry limits or are too old.

    Uses agent.delete_expired_events() to move events that have been
    attempted too many times or are stuck invisible for too long to
    the history table.
    """
    with logfire.suppress_instrumentation():
        async with (
            pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute(
                "select agent.delete_expired_events(%s, %s::int8 * interval '1m')",
                (max_attempts, max_age_minutes),
            )
