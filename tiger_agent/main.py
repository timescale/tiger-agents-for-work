import asyncio
from datetime import timedelta
from pathlib import Path

import click
import jinja2
from dotenv import find_dotenv, load_dotenv
from jinja2 import FileSystemLoader

from tiger_agent import EventHarness, TigerAgent
from tiger_agent.log_config import setup_logging


@click.group()
def cli():
    pass


@cli.command()
@click.option("--model", default="anthropic:claude-sonnet-4-5-20250929", help="AI model to use")
@click.option("--prompts", type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path), default=Path("prompts"), help="Directory containing prompt templates")
@click.option("--mcp-config", type=click.Path(exists=True, path_type=Path), default=None, help="Path to MCP config file")
@click.option("--env", type=click.Path(exists=True, path_type=Path), default=None, help="Path to environment file")
@click.option("--worker-sleep-seconds", default=60, help="Worker sleep duration in seconds")
@click.option("--worker-min-jitter-seconds", default=-15, help="Minimum jitter for worker sleep")
@click.option("--worker-max-jitter-seconds", default=15, help="Maximum jitter for worker sleep")
@click.option("--max-attempts", default=3, help="Maximum retry attempts for failed tasks")
@click.option("--max-age-minutes", default=60, help="Maximum age of an event before expiring")
@click.option("--invisibility-minutes", default=10, help="Task invisibility timeout in minutes")
@click.option("--num-workers", default=5, help="Number of worker processes")
@click.option("--rate-limit-allowed-requests", default=None, help="Number of allowed requests per user, per interval (interval is, by default 1 minute, use --rate-limit-interval to override)")
@click.option("--rate-limit-interval", default=1, help="The rate limit interval in minutes, used to determine if a user has exceeded the rate limit. Only used if --rate-limit-count is set")
def run(
    model: str,
    prompts: Path,
    mcp_config: Path | None = None,
    env: Path | None = None,
    worker_sleep_seconds: int = 60,
    worker_min_jitter_seconds: int = -15,
    worker_max_jitter_seconds: int = 15,
    max_attempts: int = 3,
    max_age_minutes: int = 60,
    invisibility_minutes: int = 10,
    num_workers: int = 5,
    rate_limit_allowed_requests: int | None = None,
    rate_limit_interval: int = 1,
):
    """Run the Tiger Agent bot"""
    # Parse integer arguments
    worker_sleep_seconds = int(worker_sleep_seconds)
    worker_min_jitter_seconds = int(worker_min_jitter_seconds)
    worker_max_jitter_seconds = int(worker_max_jitter_seconds)
    max_attempts = int(max_attempts)
    max_age_minutes = int(max_age_minutes)
    invisibility_minutes = int(invisibility_minutes)
    num_workers = int(num_workers)
    if rate_limit_allowed_requests is not None:
        rate_limit_allowed_requests = int(rate_limit_allowed_requests)
    rate_limit_interval = int(rate_limit_interval)

  

    load_dotenv(dotenv_path=env if env else find_dotenv(usecwd=True))
    setup_logging()

    # build our agent
    agent = TigerAgent(
        model=model,
        mcp_config_path=mcp_config,
        jinja_env=jinja2.Environment(
            enable_async=True,
            loader=FileSystemLoader(prompts)
        ),
        rate_limit_allowed_requests=rate_limit_allowed_requests,
        rate_limit_interval=timedelta(minutes=rate_limit_interval)
    )

    # create a harness for the processor
    harness = EventHarness(
        agent,
        worker_sleep_seconds=worker_sleep_seconds,
        worker_min_jitter_seconds=worker_min_jitter_seconds,
        worker_max_jitter_seconds=worker_max_jitter_seconds,
        max_attempts=max_attempts,
        max_age_minutes=max_age_minutes,
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
