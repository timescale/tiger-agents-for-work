from asyncio import Queue
from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool
from simple_salesforce.api import Salesforce
from slack_bolt.app.async_app import AsyncApp

from tiger_agent.slack.types import BotInfo


@dataclass
class HarnessContext:
    """Shared context provided to listeners and task processors.

    Attributes:
        app: Slack Bolt AsyncApp for making Slack API calls
        pool: Database connection pool for PostgreSQL operations
        trigger: Queue used to wake workers when new tasks are enqueued
        salesforce_client: Optional Salesforce API client
        bot_info: Bot profile information, populated after listener start
    """

    app: AsyncApp
    pool: AsyncConnectionPool
    trigger: Queue
    salesforce_client: Salesforce | None = None
    bot_info: BotInfo | None = None
    proactive_prompt_channels: list[str] | None = None
