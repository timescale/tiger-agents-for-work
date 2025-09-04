from typing import Any

import logfire
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp


@logfire.instrument("claim_event", extract_args=False)
async def claim_event(pool: AsyncConnectionPool) -> dict[str, Any] | None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute("select * from agent.claim_event()")
        return await cur.fetchone()


@logfire.instrument("delete_event", extract_args=False)
async def delete_event(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.delete_event(%s)", (event["id"],))


@logfire.instrument("run_agent", extract_args=False)
async def run_agent(app: AsyncApp, pool: AsyncConnectionPool) -> None:
    event = await claim_event(pool)

    if not event:
        return
    # create the agent
    # run the agent
    # respond to the event
    # delete the event
