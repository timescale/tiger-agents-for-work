import logfire
import psycopg
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp


async def run_agent(app: AsyncApp, pool: AsyncConnectionPool) -> None:
    logfire.info("running agent!")
