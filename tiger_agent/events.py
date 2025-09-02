import asyncio
import random
import traceback
from typing import Any, Optional

import logfire
import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck

from tiger_agent import agent


_agent_trigger = asyncio.Queue()


def diagnostic_to_dict(d: psycopg.errors.Diagnostic) -> dict[str, Any]:
    kv = {
        "column_name": d.column_name,
        "constraint_name": d.constraint_name,
        "context": d.context,
        "datatype_name": d.datatype_name,
        "internal_position": d.internal_position,
        "internal_query": d.internal_query,
        "message_detail": d.message_detail,
        "message_hint": d.message_hint,
        "message_primary": d.message_primary,
        "schema_name": d.schema_name,
        "severity": d.severity,
        "severity_nonlocalized": d.severity_nonlocalized,
        "source_file": d.source_file,
        "source_function": d.source_function,
        "source_line": d.source_line,
        "sqlstate": d.sqlstate,
        "statement_position": d.statement_position,
        "table_name": d.table_name,
    }
    return {k: v for k, v in kv.items() if v is not None}


@logfire.instrument("upsert_user", extract_args=False)
async def upsert_user(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select slack.upsert_user(%s)", (Jsonb(event),))


@logfire.instrument("upsert_channel", extract_args=False)
async def upsert_channel(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select slack.upsert_channel(%s)", (Jsonb(event),))


@logfire.instrument("insert_message", extract_args=False)
async def insert_message(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select slack.insert_message(%s)", (Jsonb(event),))


@logfire.instrument("update_message", extract_args=False)
async def update_message(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select slack.update_message(%s)", (Jsonb(event),))


@logfire.instrument("delete_message", extract_args=False)
async def delete_message(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select slack.delete_message(%s)", (Jsonb(event),))


@logfire.instrument("add_reaction", extract_args=False)
async def add_reaction(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select slack.add_reaction(%s)", (Jsonb(event),))


@logfire.instrument("remove_reaction", extract_args=False)
async def remove_reaction(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select slack.remove_reaction(%s)", (Jsonb(event),))


@logfire.instrument("insert_bot_event", extract_args=False)
async def insert_bot_event(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select slack.insert_bot_event(%s)", (Jsonb(event),))


@logfire.instrument("insert_event", extract_args=False)
async def insert_event(pool: AsyncConnectionPool, event: dict[str, Any], error: Optional[dict[str, Any]]) -> None:
    try:
        async with (
            pool.connection() as con,
            con.transaction() as _,
            con.cursor() as cur,
        ):
            await cur.execute(
                "select slack.insert_event(%s, %s)",
                (Jsonb(event), Jsonb(error))
            )
    except Exception as _:
        logfire.exception("failed to insert event", **event)


async def event_router(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    match event.get("type"):
        case "channel_created" | "channel_renamed":
            await upsert_channel(pool, event)
        case "user_change" | "user_profile_changed" | "team_join":
            await upsert_user(pool, event)
        case "reaction_added":
            await add_reaction(pool, event)
        case "reaction_removed":
            await remove_reaction(pool, event)
        case "app_mention":
            await insert_bot_event(pool, event)
            await _agent_trigger.put(True)  # signal an agent worker to service the request
        case "message":
            match event.get("subtype"):
                case None | "bot_message" | "thread_broadcast" | "file_share":
                    await insert_message(pool, event)
                case "message_changed":
                    await update_message(pool, event)
                case "message_deleted":
                    await delete_message(pool, event)
                case _:
                    logfire.warning(f"unrouted event", **event)
        case _:
            logfire.warning(f"unrouted event", **event)


async def agent_worker(app: AsyncApp, pool: AsyncConnectionPool, worker_id: int) -> None:
    while True:
        try:
            jitter = random.randint(-15, 15)
            await asyncio.wait_for(_agent_trigger.get(), timeout=(60.0 + jitter))
            logfire.info("got one!", worker_id=worker_id)
            await agent.run_agent(app, pool)
        except asyncio.TimeoutError:
            logfire.info("timeout", worker_id=worker_id)
            await agent.run_agent(app, pool)


async def initialize(app: AsyncApp, pool: AsyncConnectionPool, tasks: asyncio.TaskGroup, num_agent_workers: int = 5) -> None:
    async def event_handler(ack: AsyncAck, event: dict[str, Any]):
        event_type = event.get("type")
        with logfire.span(event_type) as _:
            await ack()
            error: Optional[dict[str, Any]] = None
            try:
                await event_router(pool, event)
            except psycopg.Error as pge:
                error = diagnostic_to_dict(pge.diag)
                logfire.exception(f"exception processing {event_type} event", **event)
            except Exception as e:
                error = {
                    'type': type(e).__name__,
                    'message': str(e),
                    'traceback': traceback.format_exc(),
                }
                logfire.exception(f"exception processing {event_type} event", **event)
            finally:
                await insert_event(pool, event, error)

    for worker_id in range(num_agent_workers):
        tasks.create_task(agent_worker(app, pool, worker_id))

    app.message("")(event_handler)
    app.event("message")(event_handler)
    app.event("app_mention")(event_handler)
    app.event("channel_created")(event_handler)
    app.event("channel_renamed")(event_handler)
    app.event("reaction_added")(event_handler)
    app.event("reaction_removed")(event_handler)
    app.event("team_join")(event_handler)
    app.event("user_change")(event_handler)
    app.event("user_profile_changed")(event_handler)
