import asyncio
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from pydantic_ai import Agent
from pydantic_ai.mcp import load_mcp_servers

from tiger_agent import AgentHarness, Event, EventContext
from tiger_agent.logging_config import setup_logging

load_dotenv(dotenv_path=find_dotenv(usecwd=True))
NAME = Path(__file__).with_suffix("").name
setup_logging(service_name=NAME)


SYSTEM_PROMPT = """\
You are a helpful Slack-native agent who answers questions posed to you in Slack messages.
"""


# load mcp server configurations from a json file
# see https://ai.pydantic.dev/mcp/client/#loading-mcp-servers-from-configuration
mcp_servers = load_mcp_servers(Path.cwd().joinpath("mcp_config.json"))


# create the pydantic-ai agent to answer questions from slack
agent = Agent(
    model=os.getenv("AGENT_MODEL", "anthropic:claude-sonnet-4-20250514"),
    name=NAME,
    system_prompt=SYSTEM_PROMPT,
    toolsets=mcp_servers,
)


# this is called when there is a question from slack
# have the agent generate an answer
# then post the answer to slack as a reply in a thread
async def respond(ctx: EventContext, event: Event):
    channel = event.event["channel"]
    ts = event.event["ts"]
    text = event.event["text"]
    async with agent as a:
        resp = await a.run(text)
    await ctx.app.client.chat_postMessage(
        channel=channel, thread_ts=ts, text=resp.output
    )


async def main() -> None:
    # create the agent harness
    harness = AgentHarness(respond)

    # run the harness
    await harness.run(num_workers=5)


if __name__ == "__main__":
    asyncio.run(main())
