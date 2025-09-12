import logfire
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp

from app.agents.eon import respond
from app.data_types import BotInfo, Mention


async def claim_event(pool: AsyncConnectionPool) -> Mention | None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute("select * from agent.claim_event()")
        result = await cur.fetchone()
        if result is None or result.get("id") is None:
            return None

        event = result.get("event")

        return Mention(
            attempts=result.get("attempts"),
            channel=event.get("channel"),
            id=result.get("id"),
            text=event.get("text"),
            thread_ts=event.get("thread_ts"),
            ts=event.get("ts"),
            tz="UTC",  # todo!
            user=event.get("user"),
            vt=result.get("vt"),
        )


@logfire.instrument("delete_event", extract_args=False)
async def delete_event(pool: AsyncConnectionPool, event: Mention) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.delete_event(%s)", (event.id,))


async def run_agent(
    app: AsyncApp, pool: AsyncConnectionPool, bot_info: BotInfo
) -> None:
    agent_event = await claim_event(pool)

    if not agent_event:
        return

    with logfire.span("Handling App Mention"):
        try:
            success = await respond(
                mention=agent_event, client=app.client, bot_info=bot_info
            )
            if success:
                await delete_event(pool, agent_event)
        except Exception as e:
            logfire.exception("Error processing agent event", error=e)
    # create the agent
    # run the agent
    # respond to the event
    # delete the event
