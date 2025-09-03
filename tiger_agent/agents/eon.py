import asyncio
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypedDict
from urllib.parse import ParseResult, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import logfire
from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits
from slack_sdk.web.async_client import AsyncWebClient

from tiger_agent.mcp_servers import slack_mcp_server

EON_MODEL = os.environ.get("EON_MODEL", "anthropic:claude-sonnet-4-0")
MAX_ATTEMPTS = 3  # only attempt to answer a mention up to this many times
TIMEOUT_MINUTES = 60  # give up on mentions older than this
INVISIBLE_MINUTES = 10  # how long we give an LLM to work on it before another can try
WORKER_SLEEP_SECONDS = 60  # how long the worker sleeps between iterations
WORKER_MIN_JITTER_SECONDS = -15
WORKER_MAX_JITTER_SECONDS = 15


assert MAX_ATTEMPTS >= 1
assert TIMEOUT_MINUTES >= 1
assert INVISIBLE_MINUTES >= 1
assert TIMEOUT_MINUTES > (INVISIBLE_MINUTES * MAX_ATTEMPTS)
assert WORKER_SLEEP_SECONDS >= 60
assert WORKER_MIN_JITTER_SECONDS < WORKER_MAX_JITTER_SECONDS
assert WORKER_SLEEP_SECONDS - WORKER_MIN_JITTER_SECONDS > 10
assert WORKER_SLEEP_SECONDS + WORKER_MAX_JITTER_SECONDS < WORKER_SLEEP_SECONDS * 2


SYSTEM_PROMPT = """\
You are {bot_name}, a member of TigerData.

TigerData is a company who provides the fastest PostgreSQL platform for real-time, analytical, and agentic applications.

You are a helpful assistant that uses sub-agents to answer questions asked of you in Slack messages.

If the question asked is too vague to answer confidently, use the tools provided to retrieve recent Slack messages in the channel/thread to see if more context can be gleaned from the conversation.

If after searching Slack, you still do not understand the question well enough to provide a confident answer, respond with one or more questions asking for clarification.

If asked to do something that falls outside your purpose or abilities (including all tooling), respond with an explanation why you refuse to carry out the ask.

Be concise, but thoroughly answer the question.

Respond in valid Markdown format, following these rules:
- DO NOT specify a language for code blocks.
- DO NOT use tildes for code blocks, always use backticks.
- DO NOT include empty lines at beginning or end of code blocks.
- DO NOT include tables
- When using block quotes, there MUST be an empty line after the block quote.
- When mentioning a slack channel or user and you know the ID, you should ONLY reference them using the format <#CHANNEL_ID> (e.g. <#C099AQDL9CZ>) for channels and <@USER_ID> (e.g. <@U123456>) for users.
- Your response MUST be less than 40,000 characters.
- For bullet points, you MUST ONLY use asterisks (*), not dashes (-), pluses (+), or any other character.
"""


def db_url_parts(url: str) -> dict[str, Any]:
    parsed: ParseResult = urlparse(url)
    return dict(
        PGHOST=parsed.hostname,
        PGDATABASE=parsed.path.lstrip("/"),
        PGPORT=str(parsed.port),
        PGUSER=parsed.username,
        PGPASSWORD=parsed.password,
    )


@dataclass
class AgentDeps:
    user_timezone: str
    bot_user_id: str


agent = Agent(
    EON_MODEL,
    deps_type=AgentDeps,
    system_prompt=SYSTEM_PROMPT.format(bot_name="eon"),
    toolsets=[slack_mcp_server()],
)


@agent.system_prompt
def add_the_date(ctx: RunContext[AgentDeps]) -> str:
    try:
        timezone = ZoneInfo(ctx.deps.user_timezone)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    return (
        f"User's current time: {datetime.now(timezone).strftime('%Y-%m-%d %H:%M:%S%z')}"
    )


@agent.system_prompt
def add_bot_user_id(ctx: RunContext[AgentDeps]) -> str:
    return f"Your Slack user ID is {ctx.deps.bot_user_id}."


@dataclass
class Mention:
    id: int
    ts: str
    channel: str
    user: str
    tz: str | None
    text: str
    thread_ts: str | None
    attempts: int
    vt: datetime


async def get_any_app_mention(con: AsyncConnection) -> Mention | None:
    """Gets zero or one app_mentions that have not been handled.

    It is possible to claim a row that is still being worked elsewhere, but that work
    would have to exceed INVISIBLE_MINUTES for this to happen.
    """
    with logfire.span("get_any_app_mention"):
        async with con.cursor(row_factory=class_row(Mention)) as cur:
            for x in range(3):  # try more than once to claim a row
                async with con.transaction() as _:
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


async def react_to_mention(
    client: AsyncWebClient, mention: Mention, emoji: str
) -> None:
    with logfire.span(
        "reacting to mention", channel=mention.channel, ts=mention.ts, emoji=emoji
    ):
        try:
            await client.reactions_add(
                channel=mention.channel, timestamp=mention.ts, name=emoji
            )
        except Exception as e:
            logfire.error(
                "error while reacting to mention",
                _exc_info=e,
                channel=mention.channel,
                ts=mention.ts,
                emoji=emoji,
            )


async def remove_reaction_from_mention(
    client: AsyncWebClient, mention: Mention, emoji: str
) -> None:
    with logfire.span(
        "removing reaction from mention",
        channel=mention.channel,
        ts=mention.ts,
        emoji=emoji,
    ):
        try:
            await client.reactions_remove(
                channel=mention.channel, timestamp=mention.ts, name=emoji
            )
        except Exception as e:
            logfire.error(
                "error while removing reaction from mention",
                _exc_info=e,
                channel=mention.channel,
                ts=mention.ts,
                emoji=emoji,
            )


async def post_response(
    client: AsyncWebClient, channel: str, thread_ts: str, text: str
) -> None:
    await client.chat_postMessage(
        channel=channel, thread_ts=thread_ts, markdown_text=text
    )


def user_prompt(mention: Mention) -> str:
    lines = []
    lines.append("<slack-message>")
    lines.append(f"<requesting-user>{mention.user}</requesting-user>")
    lines.append(f"<channel>{mention.channel}</channel>")
    lines.append(f"<ts>{mention.ts}</ts>")
    if mention.thread_ts:
        lines.append(f"<thread_ts>{mention.thread_ts}</thread_ts>")
    lines.append("</slack-message>")
    lines.append(f"Q: {mention.text}")
    return "\n".join(lines)


# See https://api.slack.com/methods/auth.test
class BotInfo(TypedDict):
    bot_id: str
    team_id: str
    user_id: str
    user: str


async def respond(
    pool: AsyncConnectionPool, client: AsyncWebClient, bot_info: BotInfo
) -> bool:
    with logfire.span("respond") as span:
        try:
            async with pool.connection() as con:
                mention = await get_any_app_mention(con)
                if not mention:
                    logfire.info("no mention found")
                    return False
                assert mention is not None
                span.set_attributes({"channel": mention.channel, "user": mention.user})
                try:
                    await react_to_mention(client, mention, "spinthinking")
                    async with get_agent(
                        bot_info["user"], bot_info["user_id"]
                    ) as agent:
                        # Slack messages are limited to 40k chars and 1 token ~= 4 chars
                        # https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them
                        # https://api.slack.com/methods/chat.postMessage#truncating
                        response = await agent.run(
                            deps=AgentDeps(user_timezone=mention.tz or "UTC"),
                            user_prompt=user_prompt(mention),
                            usage_limits=UsageLimits(response_tokens_limit=9_000),
                        )
                        await post_response(
                            client,
                            mention.channel,
                            mention.thread_ts if mention.thread_ts else mention.ts,
                            response.output,
                        )
                    await delete_app_mention(con, mention)
                    await remove_reaction_from_mention(client, mention, "spinthinking")
                    await react_to_mention(client, mention, "white_check_mark")
                    return True
                except Exception as e:
                    logfire.exception("respond failed", error_type=type(e).__name__)
                    await remove_reaction_from_mention(client, mention, "spinthinking")
                    await react_to_mention(client, mention, "x")
                    await post_response(
                        client,
                        mention.channel,
                        mention.thread_ts if mention.thread_ts else mention.ts,
                        "I experienced an issue trying to respond."
                        + " I will try again."
                        if mention.attempts < MAX_ATTEMPTS
                        else " I give up. Sorry.",
                    )
        except Exception as e:
            logfire.exception("respond failed", error_type=type(e).__name__)
        return False


async def respond_worker(
    pool: AsyncConnectionPool, client: AsyncWebClient, bot_info: BotInfo
) -> None:
    while True:
        with logfire.span("respond_worker"):
            while await respond(
                pool, client, bot_info
            ):  # while we are being successful, continue
                pass
            await delete_expired_mentions(pool)
        jitter = random.randint(WORKER_MIN_JITTER_SECONDS, WORKER_MAX_JITTER_SECONDS)
        delay = WORKER_SLEEP_SECONDS + jitter
        await asyncio.sleep(delay)
