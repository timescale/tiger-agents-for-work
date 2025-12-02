import asyncio
from datetime import timedelta
from pathlib import Path

import click
from dotenv import find_dotenv, load_dotenv

from tiger_agent import EventHarness, TigerAgent
from tiger_agent.log_config import setup_logging


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
    callback=lambda ctx, param, value: [
        ch.strip() for ch in value.split(",") if ch.strip()
    ]
    if value
    else [],
)
@click.option(
    "--disable-streaming",
    is_flag=True,
    default=False,
    help="Disable PydanticAI and Slack streaming",
)
@click.option(
    "--show-tool-call-arguments",
    is_flag=True,
    default=False,
    help="Show tool call arguments in Slack messages",
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
    disable_streaming: bool = False,
    show_tool_call_arguments: bool = False,
):
    """Run the Tiger Agent bot"""

    load_dotenv(dotenv_path=env if env else find_dotenv(usecwd=True))
    setup_logging()

    # build our agent
    agent = TigerAgent(
        model=model,
        mcp_config_path=mcp_config,
        prompt_config=[prompts] if prompts is not None else None,
        rate_limit_allowed_requests=rate_limit_allowed_requests,
        rate_limit_interval=timedelta(minutes=rate_limit_interval),
        disable_streaming=disable_streaming,
        show_tool_call_arguments=show_tool_call_arguments,
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
        proactive_prompt_channels=proactive_prompt_channels,
    )

    # run the harness
    asyncio.run(harness.run())


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
