from datetime import datetime
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from tiger_agent.customer.types import CustomerQuestionEvent
from tiger_agent.salesforce.types import (
    SalesforceAssignmentChangedEvent,
    SalesforceCaseCreatedEvent,
    SalesforceCaseStatusChangedEvent,
    SalesforceCreateNewCaseEvent,
    SalesforceFeedItemEvent,
    UserDefinedRuleMatch,
)
from tiger_agent.slack.types import (
    AgentFeedbackRatingEvent,
    AgentFeedbackRequestReminderEvent,
    BotInfo,
    ChannelInfo,
    SlackAppMentionEvent,
    SlackMessageEvent,
    SlackSalesforceCaseThreadMessageEvent,
    UserInfo,
)
from tiger_agent.tasks.types import Task


class AgentResponseContext(BaseModel):
    """Context object for AI agent responses containing event data and user information.

    This model serves as the context passed to Jinja2 templates for generating
    system and user prompts. It contains all necessary information about the
    Slack event, user details, and computed values like localized timestamps.

    Attributes:
        event: The database event record containing metadata and Slack event data
        mention: The specific app mention or message event that triggered processing
        bot: Information about the bot user (display name, user ID, etc.)
        user: Slack user information including timezone, or None if unavailable
        local_time: Event timestamp converted to user's local timezone, set automatically
    """

    task: Task
    mention: (
        SlackAppMentionEvent
        | SlackMessageEvent
        | SlackSalesforceCaseThreadMessageEvent
        | SalesforceCreateNewCaseEvent
        | SalesforceAssignmentChangedEvent
        | SalesforceCaseCreatedEvent
        | SalesforceFeedItemEvent
        | SalesforceCaseStatusChangedEvent
        | AgentFeedbackRatingEvent
        | AgentFeedbackRequestReminderEvent
        | UserDefinedRuleMatch
        | CustomerQuestionEvent
    )
    bot: BotInfo
    user: UserInfo | None = None
    local_time: datetime | None = None

    def model_post_init(self, __context):
        """Automatically compute derived fields after model initialization.

        Sets the local_time field by converting the event timestamp to the
        user's timezone if user information is available. This ensures templates
        always have access to properly localized time information.
        """
        if self.user is not None and self.user.tz is not None:
            self.local_time = self.task.event_ts.astimezone(ZoneInfo(self.user.tz))


class CaseSummary(BaseModel):
    short_description: str = Field(
        description="A brief 1-2 sentence summary of the support case issue."
    )


class SpamAssessment(BaseModel):
    """Verdict from the dedicated spam-detection agent.

    Produced by a small, tool-free model so that triaging junk costs a fraction
    of a full case investigation. See ``prompts/spam_detection_prompt.md``.
    """

    is_spam: bool = Field(
        description="True only when the case is clearly spam. When in doubt, False."
    )
    reason: str = Field(
        description="One or two sentences naming the evidence behind the verdict, recorded for auditing."
    )
    short_description: str = Field(
        description="A brief 1-2 sentence neutral summary of what the case says."
    )
    message: str = Field(
        default="",
        description="Slack mrkdwn notification body. Populated only when is_spam is True.",
    )


class DroppedStep(BaseModel):
    step: str = Field(
        description="The part of the task that was not completed, stated so it can be delegated on its own."
    )
    reason: str = Field(
        description="Why it was dropped: budget reached, tool failure, missing identifier, out of scope."
    )
    tried: list[str] = Field(
        default_factory=list,
        description="Tool calls or approaches already attempted, so a follow-up does not repeat them.",
    )
    suggested_next: str | None = Field(
        default=None,
        description="The single most promising next action, if one is known.",
    )


class InvestigationReport(BaseModel):
    """What a delegated investigator hands back to the coordinator.

    ``__str__`` is the wire format: the delegation tool returns ``str(output)``,
    so the coordinator reads this rendering, not the JSON.
    """

    answer: str = Field(
        description="Direct answer to the delegated question, one or two sentences."
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="3-8 specific findings backing the answer, citing tool names and key values. No raw tool output.",
    )
    confidence: Literal["high", "medium", "low"]
    confidence_reason: str = Field(
        description="One line on what drives the confidence level."
    )
    completed_steps: list[str] = Field(
        default_factory=list,
        description="Parts of the task that were fully carried out.",
    )
    dropped_steps: list[DroppedStep] = Field(
        default_factory=list,
        description="Parts of the task not completed. Empty when the whole task was done.",
    )
    budget_exhausted: bool = Field(
        default=False,
        description="True when the run stopped because it reached its request or token budget.",
    )

    def __str__(self) -> str:
        lines = [f"**Answer**: {self.answer}", "", "**Evidence**:"]
        if self.evidence:
            lines.extend(f"- {item}" for item in self.evidence)
        else:
            lines.append("- none")
        lines.extend(
            ["", f"**Confidence**: {self.confidence} — {self.confidence_reason}"]
        )
        if self.completed_steps:
            lines.extend(["", "**Completed steps**:"])
            lines.extend(f"- {item}" for item in self.completed_steps)
        lines.append("")
        if not self.dropped_steps:
            lines.append("**Dropped steps**: none")
        else:
            lines.append(
                "**Dropped steps** (not completed — decide whether to re-delegate):"
            )
            for dropped in self.dropped_steps:
                lines.append(f"- {dropped.step} — {dropped.reason}")
                if dropped.tried:
                    lines.append(f"  - tried: {'; '.join(dropped.tried)}")
                if dropped.suggested_next:
                    lines.append(f"  - suggested next: {dropped.suggested_next}")
        if self.budget_exhausted:
            lines.extend(
                [
                    "",
                    "**Budget exhausted**: yes — dropped steps were cut for budget, not for lack of a path.",
                ]
            )
        return "\n".join(lines)


class AgentSalesforceResponse(CaseSummary):
    """Structured response for Salesforce case events."""

    message: str
    case_owner_slack_user_id: str | None = Field(
        default=None,
        description="The Slack user ID of the case owner (e.g. 'U012AB3CD'). Null if the Slack user ID cannot be determined for the case owner.",
    )


type ExtraContextDict = dict[str, BaseModel]


class HasDestinationChannel(Protocol):
    """An event that says where the run answering it posts.

    The channel is fixed when the event is enqueued, never chosen by the
    handler: Slack events reply where they arrived, the Salesforce listener
    stamps the case channel, and a customer question has no channel at all.
    """

    @property
    def destination_channel(self) -> str | None: ...


class LinkedChannelInfo(ChannelInfo):
    linked_salesforce_account_id: str | None = None

    @property
    def is_linked_to_salesforce_account(self) -> bool:
        return self.linked_salesforce_account_id is not None
