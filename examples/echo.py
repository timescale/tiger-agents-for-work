import asyncio
import os

import logfire
from dotenv import find_dotenv, load_dotenv
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp

from tiger_agent import AgentHarness, Event, EventContext

load_dotenv(dotenv_path=find_dotenv(usecwd=True))


if os.getenv("LOGFIRE_TOKEN"):
    logfire.configure(
        service_name="echo_agent",
        service_version="0.0.1",
        scrubbing=False,
        min_level="info",
    )
    logfire.instrument_psycopg()
    logfire.instrument_pydantic_ai()


# our slackbot will just echo messages back
async def echo(ctx: EventContext, event: Event):
    channel = event.event["channel"]
    ts = event.event["ts"]
    text = event.event["text"]
    await ctx.app.client.chat_postMessage(
        channel=channel, thread_ts=ts, text=f"echo: {text}"
    )
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

    # create the pool of database connections
    async with AsyncConnectionPool(
        check=AsyncConnectionPool.check_connection,
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

        # run the harness
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(harness.run(slack_app_token, tasks, 5))


if __name__ == "__main__":
    asyncio.run(main())
