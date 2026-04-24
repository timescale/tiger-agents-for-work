import asyncio
import os
from asyncio import Queue
from datetime import timedelta
from pathlib import Path

import click
from dotenv import find_dotenv, load_dotenv
from psycopg import AsyncConnection
from slack_bolt.app.async_app import AsyncApp

from tiger_agent import TaskHarness, TigerAgent
from tiger_agent.db.utils import create_default_pool
from tiger_agent.listeners.harness import ListenerHarness
from tiger_agent.migrations import runner
from tiger_agent.salesforce.clients import get_salesforce_api_client
from tiger_agent.salesforce.types import SalesforceConfig
from tiger_agent.types import HarnessContext
from tiger_agent.utils import setup_logging


@click.group()
def cli():
    pass


@cli.command()
@click.option(
    "--model", default="anthropic:claude-sonnet-4-5-20250929", help="AI model to use"
)
@click.option(
    "--prompts",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    help="Directory containing additional and/or overriding prompt templates",
)
@click.option(
    "--mcp-config",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to MCP config file",
)
@click.option(
    "--env",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to environment file",
)
@click.option(
    "--worker-sleep-seconds",
    type=int,
    default=60,
    help="Worker sleep duration in seconds",
)
@click.option(
    "--worker-min-jitter-seconds",
    type=int,
    default=-15,
    help="Minimum jitter for worker sleep",
)
@click.option(
    "--worker-max-jitter-seconds",
    type=int,
    default=15,
    help="Maximum jitter for worker sleep",
)
@click.option(
    "--max-attempts",
    type=int,
    default=3,
    help="Maximum retry attempts for failed tasks",
)
@click.option(
    "--max-age-minutes",
    type=int,
    default=60,
    help="Maximum age of an event before expiring",
)
@click.option(
    "--invisibility-minutes",
    type=int,
    default=10,
    help="Task invisibility timeout in minutes",
)
@click.option("--num-workers", type=int, default=5, help="Number of worker processes")
@click.option(
    "--rate-limit-allowed-requests",
    type=int,
    default=None,
    help="Number of allowed requests per user, per interval (interval is, by default 1 minute, use --rate-limit-interval to override)",
)
@click.option(
    "--rate-limit-interval",
    type=int,
    default=1,
    help="The rate limit interval in minutes, used to determine if a user has exceeded the rate limit. Only used if --rate-limit-count is set",
)
@click.option(
    "--proactive-prompt-channels",
    type=str,
    default="",
    help="Comma-delimited list of channel IDs where the agent should send proactive prompts even without mentions",
    callback=lambda ctx, param, value: (
        [ch.strip() for ch in value.split(",") if ch.strip()] if value else []
    ),
)
def run(
    model: str,
    prompts: Path | None,
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
    proactive_prompt_channels: list[str] = None,
):
    """Run the Tiger Agent bot"""

    load_dotenv(dotenv_path=env if env else find_dotenv(usecwd=True))
    setup_logging()

    slack_bot_token = os.environ["SLACK_BOT_TOKEN"]

    salesforce_config = SalesforceConfig()
    salesforce_client = (
        get_salesforce_api_client() if salesforce_config.is_valid() else None
    )

    pool = create_default_pool(num_workers)
    app = AsyncApp(token=slack_bot_token, ignoring_self_events_enabled=False)
    trigger = Queue()

    hctx = HarnessContext(
        app=app,
        pool=pool,
        trigger=trigger,
        salesforce_client=salesforce_client,
        proactive_prompt_channels=proactive_prompt_channels,
    )

    # build our agent
    agent = TigerAgent(
        model=model,
        mcp_config_path=mcp_config,
        prompt_config=[prompts] if prompts is not None else None,
        rate_limit_allowed_requests=rate_limit_allowed_requests,
        rate_limit_interval=timedelta(minutes=rate_limit_interval),
    )

    # the listener harness handles external events, for instance Slack mentions or new Salesforce cases
    listener_harness = ListenerHarness(hctx=hctx, task_processor=agent)

    # the task harness handles tasks that are a result of external events
    # these tasks are stored in the agent.event table
    task_harness = TaskHarness(
        agent,
        hctx=hctx,
        worker_sleep_seconds=worker_sleep_seconds,
        worker_min_jitter_seconds=worker_min_jitter_seconds,
        worker_max_jitter_seconds=worker_max_jitter_seconds,
        max_attempts=max_attempts,
        max_age_minutes=max_age_minutes,
        invisibility_minutes=invisibility_minutes,
        num_workers=num_workers,
    )

    async def _run():
        await pool.open(wait=True)
        async with asyncio.TaskGroup() as tasks:
            await task_harness.run(tasks)
            await listener_harness.start(tasks)

    asyncio.run(_run())


@cli.command()
@click.option(
    "--env",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to environment file",
)
def migrate(
    env: Path | None = None,
):
    """Run database migrations"""
    load_dotenv(dotenv_path=env if env else find_dotenv(usecwd=True))
    setup_logging()

    async def do():
        async with await AsyncConnection.connect() as con:
            await runner.migrate_db(con)

    asyncio.run(do())


def main():
    cli()


if __name__ == "__main__":
    cli()
