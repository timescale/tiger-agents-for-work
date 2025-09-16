import asyncio
import logging
import os
import signal
from typing import Any

from dotenv import find_dotenv, load_dotenv
from psycopg import AsyncConnection

from tiger_agent import AgentHarness
from tiger_agent.agents import eon
from tiger_agent.logging_config import setup_logging

load_dotenv(dotenv_path=find_dotenv(usecwd=True))
setup_logging()

# Enable remote debugging if DEBUG environment variable is set
if os.getenv("DEBUG", "false").lower() == "true":
    import debugpy

    debugpy.listen(("0.0.0.0", 5678))
    print("🐛 Debug server started on port 5678. Waiting for debugger to attach...")
    if os.getenv("DEBUG_WAIT_FOR_ATTACH", "false").lower() == "true":
        debugpy.wait_for_client()  # Uncomment to wait for debugger before starting

logger = logging.getLogger(__name__)


def shutdown_handler(signum: int, _frame: Any):
    signame = signal.Signals(signum).name
    logger.info(f"received {signame}, exiting")
    loop = asyncio.get_running_loop()
    loop.stop()
    exit(0)


def exception_handler(_, context):
    exception = context.get("exception")
    if exception:
        logger.error("asyncio task failed", exc_info=exception, extra=context)
    else:
        logger.error("asyncio task failed", extra=context)


async def configure_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


async def reset_database_connection(con: AsyncConnection) -> None:
    await con.set_autocommit(True)


async def main() -> None:
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(exception_handler)

    harness = AgentHarness(eon.respond)

    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(harness.run(tasks))
    except* Exception as eg:
        for error in eg.exceptions:
            logger.exception("Task failed", exc_info=error)


if __name__ == "__main__":
    asyncio.run(main())
