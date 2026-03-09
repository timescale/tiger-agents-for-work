from pydantic import BaseModel


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
