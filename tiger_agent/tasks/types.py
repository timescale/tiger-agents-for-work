from asyncio import Queue
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from simple_salesforce.api import Salesforce
from slack_bolt.app.async_app import AsyncApp

from tiger_agent.salesforce.types import (
    SalesforceAssignmentChangedEvent,
    SalesforceCreateNewCaseEvent,
    SalesforceFeedItemEvent,
)
from tiger_agent.slack.types import BotInfo, SlackAppMentionEvent, SlackMessageEvent


@dataclass
class TaskContext:
    """Shared context provided to task processors.

    This context gives task processors access to the Slack app for API calls,
    the database connection pool for data operations, and the task group for
    spawning concurrent tasks.

    Attributes:
        app: Slack Bolt AsyncApp for making Slack API calls
        pool: Database connection pool for PostgreSQL operations
    """

    app: AsyncApp
    pool: AsyncConnectionPool
    salesforce_client: Salesforce | None
    slack_bot_token: str
    slack_app_token: str
    trigger: Queue
    bot_info: BotInfo | None = None


class Task(BaseModel):
    """Database representation of a task from the agent.event table.

    This model represents tasks stored in the PostgreSQL work queue table,
    including metadata for retry logic and worker coordination.

    Attributes:
        id: Primary key from agent.event table
        event_ts: Timestamp when the task was created
        attempts: Number of processing attempts made
        vt: Visibility threshold - when task becomes available for processing
        claimed: Array of timestamps when task was claimed by workers
        event: The original event payload
    """

    id: int
    event_ts: datetime
    attempts: int
    vt: datetime
    claimed: list[datetime]
    event: (
        SlackAppMentionEvent
        | SlackMessageEvent
        | SalesforceCreateNewCaseEvent
        | SalesforceAssignmentChangedEvent
        | SalesforceFeedItemEvent
    )


# Type alias for task processing callback
TaskProcessor = Callable[[TaskContext, Task], Awaitable[None]]
