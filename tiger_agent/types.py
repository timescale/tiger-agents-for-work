

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from slack_bolt.app.async_app import AsyncApp


class SlackCommand(BaseModel):
    """This represents a partial definition of the command object emitted to a handler for a slash command."""
    channel_id: str | None = None
    channel_name: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    command: str | None = None
    text: str | None = None


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

@dataclass
class CommandContext:
    """Shared context provided to the command handlers."""
    hctx: HarnessContext
    command: SlackCommand

class BaseEvent(BaseModel):
    """Base Pydantic model for Slack events.

    Represents the structure of a Slack app mention event as received from
    the Slack Events API. The model allows extra fields to accommodate
    future Slack API changes.

    Attributes:
        ts: Message timestamp
        thread_ts: Thread timestamp if this is a threaded message
        team: Slack team/workspace ID
        text: The text content of the message
        type: Event type (always 'app_mention')
        user: User ID who mentioned the app
        blocks: Slack Block Kit blocks if present
        channel: Channel ID where the mention occurred
        event_ts: Event timestamp from Slack
    """
    model_config = {"extra": "allow"}

    ts: str
    thread_ts: str | None = None
    team: str | None = None
    text: str
    type: str
    user: str
    blocks: list[dict[str, Any]] | None = None
    channel: str
    event_ts: str


class AppMentionEvent(BaseEvent):
    """Pydantic model for Slack app_mention events."""
    type: str = "app_mention"


class MessageEvent(BaseEvent):
    """Pydantic model for Slack message events."""
    type: str = "message"
    subtype: str | None = None


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
    event: AppMentionEvent | MessageEvent