import asyncio
from pathlib import Path
from random import randint

from dotenv import find_dotenv, load_dotenv

from tiger_agent import Event, EventHarness, HarnessContext
from tiger_agent.utils import setup_logging

load_dotenv(dotenv_path=find_dotenv(usecwd=True))
NAME = Path(__file__).with_suffix("").name
setup_logging(service_name=NAME)


# our slackbot will fail sometimes
async def random_fail(hctx: HarnessContext, event: Event):
    channel = event.event["channel"]
    ts = event.event["ts"]
    dice = randint(1, 6)
    msg = f"I failed. dice={dice}" if dice >= 4 else "Success"
    await hctx.app.client.chat_postMessage(channel=channel, thread_ts=ts, text=msg)
    if msg != "Success":
        raise ValueError(msg)


async def main() -> None:
    # create the agent harness
    harness = EventHarness(random_fail)
    # run the harness
    await harness.run()


if __name__ == "__main__":
    asyncio.run(main())
