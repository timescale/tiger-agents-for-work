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
@click.option("--worker-sleep-seconds", default=60, help="Worker sleep duration in seconds")
@click.option("--worker-min-jitter-seconds", default=-15, help="Minimum jitter for worker sleep")
@click.option("--worker-max-jitter-seconds", default=15, help="Maximum jitter for worker sleep")
@click.option("--max-attempts", default=3, help="Maximum retry attempts for failed tasks")
@click.option("--invisibility-minutes", default=10, help="Task invisibility timeout in minutes")
@click.option("--num-workers", default=5, help="Number of worker processes")
def run(
    model: str,
    prompts: Path,
    mcp_config: Path | None = None,
    env: Path | None = None,
    worker_sleep_seconds: int = 60,
    worker_min_jitter_seconds: int = -15,
    worker_max_jitter_seconds: int = 15,
    max_attempts: int = 3,
    invisibility_minutes: int = 10,
    num_workers: int = 5,
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
    harness = AgentHarness(
        agent,
        worker_sleep_seconds=worker_sleep_seconds,
        worker_min_jitter_seconds=worker_min_jitter_seconds,
        worker_max_jitter_seconds=worker_max_jitter_seconds,
        max_attempts=max_attempts,
        invisibility_minutes=invisibility_minutes,
        num_workers=num_workers,
    )

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
