from datetime import date

import logfire
from pydantic import BaseModel

from tiger_agent.salesforce.constants import (
    SALESFORCE_CLIENT_ID,
    SALESFORCE_CLIENT_SECRET,
    SALESFORCE_DOMAIN,
)


class CaseData(BaseModel):
    """Pydantic model for a Salesforce Case record."""

    model_config = {"extra": "allow"}

    Id: str
    CaseNumber: str | None = None
    Subject: str | None = None
    Description: str | None = None
    OwnerId: str | None = None
    Status: str | None = None
    Priority: str | None = None
    CreatedDate: str | None = None
    CreatedById: str | None = None


class SalesforceConfig(BaseModel):
    client_id: str | None = SALESFORCE_CLIENT_ID
    client_secret: str | None = SALESFORCE_CLIENT_SECRET
    domain: str | None = SALESFORCE_DOMAIN

    def is_valid(self) -> bool:
        valid = (
            self.client_id is not None
            and self.client_secret is not None
            and self.domain is not None
        )
        if not valid:
            logfire.info("Invalid Salesforce config provided")
        return valid


class SalesforceBaseEvent(BaseModel):
    """Base class for events from Salesforce"""

    type: str = "salesforce_event"
    case: CaseData
    subtype: str
    event_ts: date | None = None


class SalesforceNewCaseEvent(SalesforceBaseEvent):
    """Pydantic model for Salesforce new case event."""

    type: str = "salesforce_event"
    subtype: str = "new_case"
    case: CaseData
