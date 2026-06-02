from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, field_validator


@dataclass
class FileAttachment:
    name: str
    body: bytes
    content_type: str


@dataclass
class EmailAttachment(FileAttachment):
    pass


class SalesforceUser(BaseModel):
    Id: str | None = None
    Username: str | None = None
    FirstName: str | None = None
    LastName: str | None = None
    Email: str | None = None


class CaseData(BaseModel):
    """Pydantic model for a Salesforce Case record."""

    model_config = {"extra": "allow"}

    Id: str
    CaseNumber: str | None = None
    Cloud_Impact__c: str | None = None
    ContactEmail: str | None = None
    Customer_Slack_Thread__c: str | None = None
    Subject: str | None = None
    Description: str | None = None
    Owner: SalesforceUser | None = None
    Status: str | None = None
    Severity__c: str | None = None
    Priority: str | None = None
    CreatedDate: str | None = None
    CreatedById: str | None = None
    Cloud_Project_ID__c: str | None = None
    Cloud_Service_ID__c: str | None = None


class SalesforceBaseEvent(BaseModel):
    """Base class for events from Salesforce"""

    type: str = "salesforce_event"
    subtype: str
    event_ts: str | None = None


# this event represents the initiation of a new Salesforce
# case via Slack. We want to capture the case details, as well as
# created the case and from which channel they created it
class SalesforceCreateNewCaseEvent(SalesforceBaseEvent):
    """Pydantic model for Salesforce new case event."""

    type: str = "salesforce_event"
    subtype: str = "create_new_case"
    event_description: ClassVar[str] = (
        "A user submitted a request to open a new Salesforce support case (the case has not yet been created)"
    )
    subject: str
    description: str
    user: str
    channel: str
    severity: str
    project_id: str | None
    service_id: str | None


class SalesforceAssignmentChangedEvent(SalesforceBaseEvent):
    """Pydantic model for Salesforce new case event."""

    type: str = "salesforce_event"
    subtype: str = "new_assignee"
    event_description: ClassVar[str] = (
        "A new Salesforce support case has been created — use this to monitor for new cases"
    )
    case: CaseData
    update_link_to_thread: bool = True


class SalesforceFeedItemCreatedBy(BaseModel):
    Name: str | None = None
    Email: str | None = None


class SalesforceFeedItem(BaseModel):
    """Pydantic model for a Salesforce FeedItem SOQL record."""

    model_config = {"extra": "allow"}

    Id: str
    ParentId: str | None = None
    Body: str | None = None
    Type: str | None = None
    CreatedDate: str | None = None
    CreatedById: str | None = None
    CreatedBy: SalesforceFeedItemCreatedBy | None = None


class SalesforceEmailMessage(SalesforceFeedItem):
    """Pydantic model for a Salesforce EmailMessage record."""

    Subject: str | None = None
    HasAttachment: bool | None = None
    Type: str | None = "EmailMessage"
    HtmlBody: str | None = None


# at present, we are using these to synchronize
# comments made on cases with a Slack thread
# that is linked to the case
class SalesforceFeedItemEvent(SalesforceBaseEvent):
    """Pydantic model for a new Salesforce FeedItem (Chatter post) on a case."""

    type: str = "salesforce_event"
    subtype: str = "new_feed_item"
    event_description: ClassVar[str] = (
        "A new message posted to a Salesforce case feed (e.g. a customer reply or engineer response)"
    )
    feed_item: SalesforceFeedItem


class SalesforceCaseStatusChangedEvent(SalesforceBaseEvent):
    """Pydantic model for a Salesforce case closed event."""

    type: str = "salesforce_event"
    subtype: str = "case_status_changed"
    event_description: ClassVar[str] = (
        "A Salesforce case status changed (e.g. New → In Progress → Closed)"
    )
    case: CaseData
    slack_thread_ts: str | None = None
    slack_channel_id: str | None = None


class AgentFeedbackRatingSubtype(StrEnum):
    internal = "internal"
    external = "external"


class AgentFeedbackRatingEvent(BaseModel):
    type: str = "agent_feedback_rating"
    subtype: AgentFeedbackRatingSubtype = AgentFeedbackRatingSubtype.internal
    event_description: ClassVar[str] = (
        "A user submitted a feedback rating via the in-app feedback form"
    )

    # the agent message that was rated
    message_ts: str | None = None
    channel: str
    rating: int | None = None
    description: str | None = None
    user: str | None = None


@dataclass
class ServiceRecord:
    service_id: str
    project_id: str | None


class UserDefinedRule(BaseModel):
    id: int
    name: str
    owner_slack_id: str
    event_type: str
    event_subtype: str | None = None
    criteria: str
    criteria_examples: list[str] = []
    action_prompt: str
    enabled: bool = True

    @field_validator("criteria_examples", mode="before")
    @classmethod
    def coerce_none_to_empty_list(cls, v: object) -> object:
        return v or []


class UserDefinedRuleMatch(BaseModel):
    type: str = "custom_rule_match"
    rule_id: int
    rule_name: str
    owner_slack_id: str
    action_prompt: str
    matched_event: dict
    match_reason: str


class ContentVersion(BaseModel):
    """Pydantic model for a Salesforce ContentVersion record."""

    model_config = {"extra": "allow"}

    Id: str
    Title: str | None = None
    FileExtension: str | None = None
    VersionData: str | None = None
    ContentDocumentId: str | None = None
    IsLatest: bool | None = None
