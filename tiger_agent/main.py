import asyncio
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from tiger_agent import AgentHarness, TigerAgent
from tiger_agent.logging import setup_logging

load_dotenv(dotenv_path=find_dotenv(usecwd=True))
setup_logging(service_name="eon")


async def main() -> None:
    # build our agent
    agent = TigerAgent(
        model="anthropic:claude-sonnet-4-20250514",
        mcp_config_path=Path.cwd().joinpath("mcp_config.json"),
    )

    # create the agent harness for the event processor
    harness = AgentHarness(agent)

    # run the harness
    await harness.run()


if __name__ == "__main__":
    asyncio.run(main())
