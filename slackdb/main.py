import asyncio
import os
import signal
from typing import Any

from dotenv import load_dotenv, find_dotenv
import logfire
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from slack_bolt.adapter.socket_mode.websockets import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp

from slackdb import __version__
from .migrations.runner import migrate_db
from .ingest import register_event_handlers

load_dotenv(dotenv_path=find_dotenv(usecwd=True))


logfire.configure(
    service_name=os.getenv("SERVICE_NAME", "eon"),
    service_version=__version__,
)
logfire.instrument_psycopg()
logfire.instrument_pydantic_ai()
logfire.instrument_mcp()
logfire.instrument_httpx()
logfire.instrument_system_metrics({
    'process.cpu.time': ['user', 'system'],
    'process.cpu.utilization': None,
    'process.cpu.core_utilization': None,
    'process.memory.usage': None,
    'process.memory.virtual': None,
    'process.thread.count': None,
})


def shutdown_handler(signum: int, _frame: Any):
    signame = signal.Signals(signum).name
    logfire.info(f"received {signame}, exiting")
    exit(0)


def exception_handler(_, context):
    with logfire.span("asyncio loop exception") as _:
        exception = context.get('exception')
        if exception:
            logfire.error('asyncio task failed', _exc_info=exception, **context)
        else:
            logfire.error('asyncio task failed', **context)


async def configure_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


async def reset_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


async def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    assert database_url is not None, "DATABASE_URL environment variable is missing!"
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    assert slack_bot_token is not None, "SLACK_BOT_TOKEN environment variable is missing!"
    slack_app_token = os.getenv("SLACK_APP_TOKEN")
    assert slack_app_token is not None, "SLACK_APP_TOKEN environment variable is missing!"
    
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(exception_handler)
    
    async with AsyncConnectionPool(
        database_url,
        check=AsyncConnectionPool.check_connection,
        configure=configure_database_connection,
        reset=reset_database_connection,
    ) as pool:
        await pool.wait()

        with pool.connection() as con:
            await migrate_db(con)

        slack_app = AsyncApp(
            token=slack_bot_token,
            ignoring_self_events_enabled=False,
        )
        
        await register_event_handlers(slack_app, pool)

        handler = AsyncSocketModeHandler(slack_app, slack_app_token)
        
        await asyncio.gather(handler.start_async())


if __name__ == "__main__":
    asyncio.run(main())
