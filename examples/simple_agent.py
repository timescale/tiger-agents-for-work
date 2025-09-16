import asyncio
import os
from pathlib import Path

import logfire
from dotenv import find_dotenv, load_dotenv
from pydantic_ai import Agent
from pydantic_ai.mcp import load_mcp_servers

from tiger_agent import AgentHarness, Event, EventContext

load_dotenv(dotenv_path=find_dotenv(usecwd=True))


from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp

if os.getenv("LOGFIRE_TOKEN"):
    logfire.configure(
        service_name="simple_agent",
        service_version="0.0.1",
        scrubbing=False,
        min_level="info",
    )
    logfire.instrument_psycopg()
    logfire.instrument_pydantic_ai()


SYSTEM_PROMPT="""\
You are a helpful Slack-native agent who answers questions posed to you in Slack messages.
"""


# load mcp server configurations from a json file
# see https://ai.pydantic.dev/mcp/client/#loading-mcp-servers-from-configuration
mcp_servers = load_mcp_servers(Path.cwd().joinpath("mcp_config.json"))


# create the pydantic-ai agent to answer questions from slack
agent = Agent(
    model=os.getenv("AGENT_MODEL", "anthropic:claude-sonnet-4-20250514"),
    name="simple-agent",
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
    await ctx.app.client.chat_postMessage(channel=channel, thread_ts=ts, text=resp.output)


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
        harness = AgentHarness(app, pool, respond)

        # run the harness
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(harness.run(slack_app_token, tasks, 5))


if __name__ == "__main__":
    asyncio.run(main())
