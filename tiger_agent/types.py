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
        proactive_prompt_channels: Channel IDs where proactive prompts are sent without mentions
        num_workers: Number of concurrent worker tasks
        worker_sleep_seconds: Base sleep time between worker polling cycles
        worker_min_jitter_seconds: Minimum random jitter applied to worker sleep
        worker_max_jitter_seconds: Maximum random jitter applied to worker sleep
        max_attempts: Maximum retry attempts per task before expiring
        max_age_minutes: Maximum age of a task before it is expired
        invisibility_minutes: How long a claimed task remains invisible to other workers
    """

    app: AsyncApp
    pool: AsyncConnectionPool
    trigger: Queue
    salesforce_client: Salesforce | None = None
    bot_info: BotInfo | None = None
    proactive_prompt_channels: list[str] | None = None
    num_workers: int = 5
    worker_sleep_seconds: int = 60
    worker_min_jitter_seconds: int = -15
    worker_max_jitter_seconds: int = 15
    max_attempts: int = 3
    max_age_minutes: int = 60
    invisibility_minutes: int = 10
