"""Registry of all event types that can be targeted by custom rules.

Each class in EVENT_TYPE_REGISTRY must have:
- type: str class field with a default value
- subtype: str class field with a default value, or absent (treated as None)
- event_description: ClassVar[str] describing when this event fires
"""

from tiger_agent.salesforce.types import (
    SalesforceAssignmentChangedEvent,
    SalesforceCaseStatusChangedEvent,
    SalesforceCreateNewCaseEvent,
    SalesforceFeedItemEvent,
)
from tiger_agent.slack.types import (
    AgentFeedbackRatingEvent,
    SlackAppMentionEvent,
    SlackMessageEvent,
    SlackSalesforceCaseThreadMessageEvent,
)

EVENT_TYPE_REGISTRY: list[type] = [
    SlackAppMentionEvent,
    SlackMessageEvent,
    SlackSalesforceCaseThreadMessageEvent,
    SalesforceCreateNewCaseEvent,
    SalesforceAssignmentChangedEvent,
    SalesforceFeedItemEvent,
    SalesforceCaseStatusChangedEvent,
    AgentFeedbackRatingEvent,
]
