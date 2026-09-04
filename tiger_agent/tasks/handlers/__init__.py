"""Task handlers package.

Re-exports the dispatch base classes and every concrete handler so callers can
continue to write `from tiger_agent.tasks.handlers import ...`.
"""

from tiger_agent.agent.limits import AGENT_USAGE_LIMITS
from tiger_agent.tasks.handlers.agent_feedback_rating import AgentFeedbackRatingHandler
from tiger_agent.tasks.handlers.agent_feedback_request_reminder import (
    AgentFeedbackRequestReminderHandler,
)
from tiger_agent.tasks.handlers.base import (
    TaskHandler,
    TaskProcessor,
)
from tiger_agent.tasks.handlers.salesforce_assignment_changed import (
    SalesforceAssignmentChangedHandler,
)
from tiger_agent.tasks.handlers.salesforce_case_created import (
    SalesforceCaseCreatedHandler,
)
from tiger_agent.tasks.handlers.salesforce_case_status_changed import (
    SalesforceCaseStatusChangedHandler,
)
from tiger_agent.tasks.handlers.salesforce_create_case import (
    SalesforceCreateCaseHandler,
)
from tiger_agent.tasks.handlers.salesforce_feed_item import SalesforceFeedItemHandler
from tiger_agent.tasks.handlers.slack import SlackTaskHandler
from tiger_agent.tasks.handlers.slack_salesforce_case_thread_message import (
    SlackSalesforceCaseThreadMessageHandler,
)
from tiger_agent.tasks.handlers.user_defined_rule_match import (
    UserDefinedRuleMatchHandler,
)

__all__ = [
    "AGENT_USAGE_LIMITS",
    "AgentFeedbackRatingHandler",
    "AgentFeedbackRequestReminderHandler",
    "SalesforceAssignmentChangedHandler",
    "SalesforceCaseCreatedHandler",
    "SalesforceCaseStatusChangedHandler",
    "SalesforceCreateCaseHandler",
    "SalesforceFeedItemHandler",
    "SlackSalesforceCaseThreadMessageHandler",
    "SlackTaskHandler",
    "TaskHandler",
    "TaskProcessor",
    "UserDefinedRuleMatchHandler",
]
