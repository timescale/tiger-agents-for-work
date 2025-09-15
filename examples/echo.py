import asyncio
import os
import signal
from typing import Any

import logfire
from dotenv import find_dotenv, load_dotenv

from tiger_agent import Event, AgentHarness, EventContext

load_dotenv(dotenv_path=find_dotenv(usecwd=True))


from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp


logfire.configure(
    service_name="echo_agent",
    service_version="0.0.1",
    scrubbing=False,
    min_level="info",
)
logfire.instrument_psycopg()
logfire.instrument_pydantic_ai()


def shutdown_handler(signum: int, _frame: Any):
    signame = signal.Signals(signum).name
    loop = asyncio.get_running_loop()
    loop.stop()
    logfire.info(f"Received {signame}, exiting")
    exit(0)


def exception_handler(_, context):
    with logfire.span("asyncio loop exception") as _:
        exception = context.get("exception")
        if exception:
            logfire.error("asyncio task failed", _exc_info=exception, **context)
        else:
            logfire.error("asyncio task failed", **context)


async def configure_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


async def reset_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


# our slackbot will just echo messages back
async def echo(ctx: EventContext, event: Event):
    channel = event.event["channel"]
    ts = event.event["ts"]
    text = event.event["text"]
    await ctx.app.client.chat_postMessage(channel=channel, thread_ts=ts, text=f"echo: {text}")
    logfire.info(f"responded to event {event.id}")


async def main() -> None:
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    assert slack_bot_token is not None, (
        "SLACK_BOT_TOKEN environment variable is missing!"
    )
    slack_app_token = os.getenv("SLACK_APP_TOKEN")
    assert slack_app_token is not None, (
        "SLACK_APP_TOKEN environment variable is missing!"
    )

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(exception_handler)

    # create the pool of database connections
    async with AsyncConnectionPool(
            check=AsyncConnectionPool.check_connection,
            configure=configure_database_connection,
            reset=reset_database_connection,
    ) as pool:
        # wait for the connections to be ready
        await pool.wait()

        # create a slack app
        app = AsyncApp(
            token=slack_bot_token,
            ignoring_self_events_enabled=False,
        )

        # create the agent harness
        harness = AgentHarness(app, pool, echo)

        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(harness.run(slack_app_token, tasks, 5))
        except* Exception as eg:
            for error in eg.exceptions:
                logfire.exception("Task failed", error=error)


if __name__ == "__main__":
    asyncio.run(main())
