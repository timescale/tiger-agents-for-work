from pydantic import BaseModel


class NewSalesforceCaseFormSubmission(BaseModel):
    subject: str
    description: str
    service: str | None = None
    customer_impact: str | None = None
