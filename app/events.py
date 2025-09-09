import asyncio
import random
from typing import Any

import logfire
import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck

from app import agent
from app.data_types import BotInfo

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


@logfire.instrument("insert_event", extract_args=False)
async def insert_event(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.insert_event(%s)", (Jsonb(event),))


async def event_router(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    match event.get("type"):
        case "app_mention":
            await insert_event(pool, event)
            await _agent_trigger.put(
                True
            )  # signal an agent worker to service the request
        case _:
            logfire.warning("unrouted event", **event)


async def agent_worker(
    app: AsyncApp, pool: AsyncConnectionPool, worker_id: int, bot_info: BotInfo
) -> None:
    while True:
        try:
            jitter = random.randint(-15, 15)
            await asyncio.wait_for(_agent_trigger.get(), timeout=(60.0 + jitter))
            logfire.debug("got one!", worker_id=worker_id)
            await agent.run_agent(app, pool, bot_info)
        except TimeoutError:
            logfire.debug("timeout", worker_id=worker_id)
            await agent.run_agent(app, pool, bot_info=bot_info)


async def initialize(
    app: AsyncApp,
    pool: AsyncConnectionPool,
    tasks: asyncio.TaskGroup,
    bot_info: BotInfo,
    num_agent_workers: int = 5,
) -> None:
    async def event_handler(ack: AsyncAck, event: dict[str, Any]):
        event_type = event.get("type")
        with logfire.span(event_type):
            await ack()
            try:
                await event_router(pool, event)
            except Exception:
                logfire.exception(f"exception processing {event_type} event", **event)

    for worker_id in range(num_agent_workers):
        tasks.create_task(agent_worker(app, pool, worker_id, bot_info))

    app.event("app_mention")(event_handler)
    @app.event("message")
    async def handle_message(ack):
        await ack()
