import asyncio
import os

import logfire
from dotenv import find_dotenv, load_dotenv

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
    # create the agent harness
    harness = AgentHarness(echo)
    # run the harness
    await harness.run(num_workers=5)


if __name__ == "__main__":
    asyncio.run(main())
