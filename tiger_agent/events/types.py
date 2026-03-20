from asyncio import Queue
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from slack_bolt.app.async_app import AsyncApp

from tiger_agent.salesforce.types import (
    SalesforceAssignmentChangedEvent,
    SalesforceNewCaseEvent,
)
from tiger_agent.slack.types import SlackAppMentionEvent, SlackMessageEvent


@dataclass
class HarnessContext:
    """Shared context provided to event processors.

    This context gives event processors access to the Slack app for API calls,
    the database connection pool for data operations, and the task group for
    spawning concurrent tasks.

    Attributes:
        app: Slack Bolt AsyncApp for making Slack API calls
        pool: Database connection pool for PostgreSQL operations
    """

    app: AsyncApp
    pool: AsyncConnectionPool
    slack_bot_token: str
    slack_app_token: str
    trigger: Queue


class Event(BaseModel):
    """Database representation of an event from the agent.event table.

    This model represents events stored in the PostgreSQL work queue table,
    including metadata for retry logic and worker coordination.

    Attributes:
        id: Primary key from agent.event table
        event_ts: Timestamp when the event occurred
        attempts: Number of processing attempts made
        vt: Visibility threshold - when event becomes available for processing
        claimed: Array of timestamps when event was claimed by workers
        event: The original Slack app mention event data
    """

    id: int
    event_ts: datetime
    attempts: int
    vt: datetime
    claimed: list[datetime]
    event: (
        SlackAppMentionEvent
        | SlackMessageEvent
        | SalesforceNewCaseEvent
        | SalesforceAssignmentChangedEvent
    )


# Type alias for event processing callback
EventProcessor = Callable[[HarnessContext, Event], Awaitable[None]]
