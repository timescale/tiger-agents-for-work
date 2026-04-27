from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

from tiger_agent.salesforce.types import (
    SalesforceAssignmentChangedEvent,
    SalesforceCreateNewCaseEvent,
    SalesforceFeedItemEvent,
)
from tiger_agent.slack.types import SlackAppMentionEvent, SlackMessageEvent
from tiger_agent.types import HarnessContext


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
TaskProcessor = Callable[[HarnessContext, Task], Awaitable[None]]
