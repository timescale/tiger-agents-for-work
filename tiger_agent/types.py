import os
from asyncio import Event, Queue
from dataclasses import dataclass, field

from psycopg_pool import AsyncConnectionPool
from simple_salesforce.api import Salesforce
from slack_bolt.app.async_app import AsyncApp

from tiger_agent.slack.types import BotInfo


@dataclass
class HarnessContext:
    """Shared context provided to listeners and task processors.

    Prefer ``HarnessContext.create()`` to build one; it wires up the Slack app,
    database pool, Salesforce client, and fetches bot info in a single async step.

    Attributes:
        app: Slack Bolt AsyncApp for making Slack API calls
        pool: Database connection pool for PostgreSQL operations
        trigger: Queue used to wake workers when new tasks are enqueued
        shutdown: Event set when a soft shutdown has been requested; workers stop
            claiming new tasks and listeners disconnect once it is set
        bot_info: Bot profile information
        salesforce_client: Optional Salesforce API client
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
    bot_info: BotInfo
    salesforce_client: Salesforce | None = None
    proactive_prompt_channels: list[str] | None = None
    num_workers: int = 5
    worker_sleep_seconds: int = 60
    worker_min_jitter_seconds: int = -15
    worker_max_jitter_seconds: int = 15
    max_attempts: int = 3
    max_age_minutes: int = 60
    invisibility_minutes: int = 10
    shutdown: Event = field(default_factory=Event)

    @classmethod
    async def create(
        cls,
        *,
        slack_token: str | None = None,
        proactive_prompt_channels: list[str] | None = None,
        num_workers: int = 5,
        worker_sleep_seconds: int = 60,
        worker_min_jitter_seconds: int = -15,
        worker_max_jitter_seconds: int = 15,
        max_attempts: int = 3,
        max_age_minutes: int = 60,
        invisibility_minutes: int = 10,
    ) -> "HarnessContext":
        # Local imports avoid an import cycle: db.utils and salesforce.clients
        # both transitively depend on this module.
        from tiger_agent.db.utils import create_default_pool
        from tiger_agent.salesforce.clients import get_salesforce_api_client
        from tiger_agent.slack.utils import fetch_bot_info

        app = AsyncApp(
            token=slack_token or os.environ["SLACK_BOT_TOKEN"],
            ignoring_self_events_enabled=False,
        )
        return cls(
            app=app,
            pool=create_default_pool(num_workers),
            trigger=Queue(),
            bot_info=await fetch_bot_info(app.client),
            salesforce_client=get_salesforce_api_client(),
            proactive_prompt_channels=proactive_prompt_channels,
            num_workers=num_workers,
            worker_sleep_seconds=worker_sleep_seconds,
            worker_min_jitter_seconds=worker_min_jitter_seconds,
            worker_max_jitter_seconds=worker_max_jitter_seconds,
            max_attempts=max_attempts,
            max_age_minutes=max_age_minutes,
            invisibility_minutes=invisibility_minutes,
        )
