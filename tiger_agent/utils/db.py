import asyncio

import logfire
from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool

from tiger_agent.agents.types import Mention

# Constants from eon.py
MAX_ATTEMPTS = 3  # only attempt to answer a mention up to this many times
TIMEOUT_MINUTES = 60  # give up on mentions older than this
INVISIBLE_MINUTES = 10  # how long we give an LLM to work on it before another can try


async def get_any_app_mention(con: AsyncConnection) -> Mention | None:
    """Gets zero or one app_mentions that have not been handled.

    It is possible to claim a row that is still being worked elsewhere, but that work
    would have to exceed INVISIBLE_MINUTES for this to happen.
    """
    with logfire.span("get_any_app_mention"):
        async with con.cursor(row_factory=class_row(Mention)) as cur:
            for _ in range(3):  # try more than once to claim a row
                async with con.transaction():
                    await cur.execute(
                        """\
                        with x as
                        (
                            select *
                            from slack.mention m
                            where m.vt <= clock_timestamp() -- must be visible
                            and m.attempts < %(max_attempts)s -- must not have exceeded attempts
                            order by random() -- shuffle the deck
                            limit 1
                            for update
                            skip locked
                        )
                        , u as
                        (
                            update slack.mention u set
                              vt = clock_timestamp() + (%(invisible_minutes)s * interval '1m') -- invisible for a bit while we work it
                            , attempts = u.attempts + 1
                            from x
                            where u.id = x.id
                            returning u.*
                        )
                        select
                          u.id
                        , u.event->>'ts' as ts
                        , u.event->>'channel' as channel
                        , u.event->>'user' as "user"
                        , su.tz
                        , u.event->>'text' as text
                        , u.event->>'thread_ts' as thread_ts
                        , u.attempts
                        , u.vt
                        from u
                        left join slack.user as su on u.event->>'user' = su.id
                    """,
                        dict(
                            max_attempts=MAX_ATTEMPTS,
                            invisible_minutes=INVISIBLE_MINUTES,
                        ),
                    )
                    row = await cur.fetchone()
                    if row:
                        return row
                logfire.debug("didn't find one. sleeping...")
                await asyncio.sleep(5)  # we didn't get one. wait a bit and try again
        return None  # nothing to do


async def delete_app_mention(con: AsyncConnection, mention: Mention) -> None:
    with logfire.span("delete_app_mention"):
        """Deletes a specific mention"""
        async with (
            con.cursor() as cur,
            con.transaction() as _,
        ):
            await cur.execute(
                """\
                with x as
                (
                    delete from slack.mention d
                    where d.id = %(id)s
                    returning *
                )
                insert into slack.mention_hist
                ( id
                , event_ts
                , attempts
                , vt
                , event
                )
                select
                  x.id
                , x.event_ts
                , x.attempts
                , x.vt
                , x.event
                from x
            """,
                dict(
                    id=mention.id,
                ),
            )


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
                delete from slack.mention d
                where d.vt <= (now() - (%(vt_timeout)s * interval '1m')) -- too old
            """,
                dict(
                    vt_timeout=TIMEOUT_MINUTES,
                    max_attempts=MAX_ATTEMPTS,
                ),
            )
            logfire.info(f"found {cur.rowcount} expired/dead mentions. deleted.")