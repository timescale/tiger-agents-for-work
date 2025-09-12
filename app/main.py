import asyncio
import os
import signal
from typing import Any

import logfire
from dotenv import find_dotenv, load_dotenv

# Enable remote debugging if DEBUG environment variable is set
if os.getenv("DEBUG", "false").lower() == "true":
    import debugpy

    debugpy.listen(("0.0.0.0", 5678))
    print("🐛 Debug server started on port 5678. Waiting for debugger to attach...")
    if os.getenv("DEBUG_WAIT_FOR_ATTACH", "false").lower() == "true":
        debugpy.wait_for_client()  # Uncomment to wait for debugger before starting

from events import initialize
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from slack_bolt.adapter.socket_mode.websockets import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp

from migrations.runner import migrate_db

from . import AGENT_NAME, __version__

load_dotenv(dotenv_path=find_dotenv(usecwd=True))

logfire.configure(
    service_name=os.getenv("SERVICE_NAME", AGENT_NAME),
    service_version=__version__,
    scrubbing=False,
    min_level="info",
)
# logfire.instrument_psycopg()
logfire.instrument_pydantic_ai()
# logfire.instrument_mcp()
# logfire.instrument_httpx()
# logfire.instrument_system_metrics(
#     {
#         "process.cpu.time": ["user", "system"],
#         "process.cpu.utilization": None,
#         "process.cpu.core_utilization": None,
#         "process.memory.usage": None,
#         "process.memory.virtual": None,
#         "process.thread.count": None,
#     }
# )


def shutdown_handler(signum: int, _frame: Any):
    signame = signal.Signals(signum).name
    logfire.info(f"Received {signame}, exiting")
    exit(0)


def exception_handler(_, context):
    with logfire.span("asyncio loop exception") as _:
        exception = context.get("exception")
        if exception:
            logfire.error("asyncio task failed", _exc_info=exception, **context)
        else:
            logfire.error("asyncio task failed", **context)


async def configure_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


async def reset_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


async def main() -> None:
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    assert slack_bot_token is not None, (
        "SLACK_BOT_TOKEN environment variable is missing!"
    )
    slack_app_token = os.getenv("SLACK_APP_TOKEN")
    assert slack_app_token is not None, (
        "SLACK_APP_TOKEN environment variable is missing!"
    )

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(exception_handler)

    async with AsyncConnectionPool(
        check=AsyncConnectionPool.check_connection,
        configure=configure_database_connection,
        reset=reset_database_connection,
    ) as pool:
        await pool.wait()

        async with pool.connection() as con:
            await migrate_db(con)

        app = AsyncApp(
            token=slack_bot_token,
            ignoring_self_events_enabled=False,
        )

        handler = AsyncSocketModeHandler(app, slack_app_token)
        slack_client = app.client
        bot_info = await slack_client.auth_test()

        try:
            async with asyncio.TaskGroup() as tasks:
                await initialize(app, pool, tasks, bot_info, num_agent_workers=5)
                tasks.create_task(handler.start_async())
        except* Exception as eg:
            for error in eg.exceptions:
                logfire.exception("Task failed", error=error)


if __name__ == "__main__":
    asyncio.run(main())
