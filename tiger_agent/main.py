import asyncio
from pathlib import Path

import click
from dotenv import find_dotenv, load_dotenv

from tiger_agent.logging import setup_logging


@click.group()
def cli():
    pass


@cli.command()
@click.option("--model", default="anthropic:claude-sonnet-4-20250514", help="AI model to use")
@click.option("--prompts", type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path), default=Path("prompts"), help="Directory containing prompt templates")
@click.option("--mcp-config", type=click.Path(exists=True, path_type=Path), default=None, help="Path to MCP config file")
@click.option("--env", type=click.Path(exists=True, path_type=Path), default=None, help="Path to environment file")
def run(
    model: str,
    prompts: Path,
    mcp_config: Path | None = None,
    env: Path | None = None,
):
    """Run the Tiger Agent bot"""
    import jinja2
    from jinja2 import FileSystemLoader

    from tiger_agent import AgentHarness, TigerAgent

    load_dotenv(dotenv_path=env if env else find_dotenv(usecwd=True))
    setup_logging()

    # build our agent
    agent = TigerAgent(
        model=model,
        mcp_config_path=mcp_config,
        jinja_env=jinja2.Environment(
            enable_async=True,
            loader=FileSystemLoader(prompts)
        )
    )

    # create the agent harness for the event processor
    harness = AgentHarness(agent)

    # run the harness
    asyncio.run(harness.run())


@cli.command()
@click.option("--env", type=click.Path(exists=True, path_type=Path), default=None, help="Path to environment file")
def migrate(
    env: Path | None = None,
):
    """Run database migrations"""
    load_dotenv(dotenv_path=env if env else find_dotenv(usecwd=True))
    setup_logging()

    from psycopg import AsyncConnection

    from tiger_agent.migrations import runner
    
    async def do():
        async with await AsyncConnection.connect() as con:
            await runner.migrate_db(con)

    asyncio.run(do())


def main():
    cli()


if __name__ == "__main__":
    cli()
