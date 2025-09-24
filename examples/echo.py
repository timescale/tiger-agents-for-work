import asyncio
from pathlib import Path

import logfire
from dotenv import find_dotenv, load_dotenv

from tiger_agent import EventHarness, Event, HarnessContext
from tiger_agent.log_config import setup_logging

load_dotenv(dotenv_path=find_dotenv(usecwd=True))
NAME = Path(__file__).with_suffix("").name
setup_logging(service_name=NAME)


# our slackbot will just echo messages back
async def echo(ctx: HarnessContext, event: Event):
    channel = event.event["channel"]
    ts = event.event["ts"]
    text = event.event["text"]
    await ctx.app.client.chat_postMessage(
        channel=channel, thread_ts=ts, text=f"echo: {text}"
    )
    logfire.info(f"responded to event {event.id}")


async def main() -> None:
    # create the agent harness
    harness = EventHarness(echo)
    # run the harness
    await harness.run()


if __name__ == "__main__":
    asyncio.run(main())
