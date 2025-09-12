import logfire
from psycopg_pool import AsyncConnectionPool

# Constants from eon.py
MAX_ATTEMPTS = 3  # only attempt to answer a mention up to this many times
TIMEOUT_MINUTES = 60  # give up on mentions older than this
INVISIBLE_MINUTES = 10  # how long we give an LLM to work on it before another can try


async def delete_expired_mentions(pool: AsyncConnectionPool) -> None:
    """Deletes any mention that has expired"""
    with logfire.span("delete_expired_mentions"):
        async with (
            pool.connection() as con,
            con.cursor() as cur,
            con.transaction() as _,
        ):
            await cur.execute(
                """\
                -- delete them. trigger "moves" them to mention_hist table
                delete from agent.event d
                where d.vt <= (now() - (%(vt_timeout)s * interval '1m')) -- too old
            """,
                dict(
                    vt_timeout=TIMEOUT_MINUTES,
                    max_attempts=MAX_ATTEMPTS,
                ),
            )
            logfire.info(f"found {cur.rowcount} expired/dead mentions. deleted.")
