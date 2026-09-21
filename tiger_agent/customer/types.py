from typing import ClassVar, Literal

from pydantic import BaseModel


class CustomerQuestionEvent(BaseModel):
    """A question from a customer, with no Slack channel behind it.

    Runs for this event are customer-facing: only MCP servers that are not
    ``internal_only`` are loaded and no Slack tools are given, whatever the
    deployment's other settings. Nothing enqueues it in production yet; the
    eval suite builds one per Salesforce case it replays.
    """

    type: Literal["customer_question"] = "customer_question"
    event_description: ClassVar[str] = (
        "A customer asked a support question outside Slack (e.g. a Salesforce case)"
    )
    text: str
    subject: str | None = None
    platform: str | None = None
    product_area: str | None = None
    source: str | None = None
    source_id: str | None = None
    event_ts: str | None = None

    @property
    def destination_channel(self) -> None:
        """No Slack destination: the reply goes back to whoever asked."""
        return None
