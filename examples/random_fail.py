import asyncio
import os
from random import randint

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


# our slackbot will fail sometimes
async def random_fail(ctx: EventContext, event: Event):
    channel = event.event["channel"]
    ts = event.event["ts"]
    dice = randint(1, 6)
    msg = "I failed" if dice == 6 else "Success"
    await ctx.app.client.chat_postMessage(
        channel=channel, thread_ts=ts, text=msg
    )
    if dice == 6:
        raise ValueError("dice roll was 6")


async def main() -> None:
    # create the agent harness
    harness = AgentHarness(random_fail)
    # run the harness
    await harness.run(num_workers=5)


if __name__ == "__main__":
    asyncio.run(main())
