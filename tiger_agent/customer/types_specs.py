from datetime import UTC, datetime

from tiger_agent.customer.types import CustomerQuestionEvent
from tiger_agent.events import EVENT_TYPE_REGISTRY, EVENT_TYPES_BY_NAME
from tiger_agent.salesforce.types import (
    SalesforceAssignmentChangedEvent,
    SalesforceCreateNewCaseEvent,
)
from tiger_agent.slack.types import SlackAppMentionEvent
from tiger_agent.tasks.types import Task

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _task(event: dict) -> Task:
    return Task(id=1, event_ts=NOW, attempts=0, vt=NOW, claimed=[], event=event)


class TestCustomerQuestionEvent:
    def test_is_registered_for_rules(self):
        assert CustomerQuestionEvent in EVENT_TYPE_REGISTRY
        assert EVENT_TYPES_BY_NAME["CustomerQuestionEvent"] is CustomerQuestionEvent

    def test_has_no_slack_destination(self):
        event = CustomerQuestionEvent(text="How do I add a retention policy?")
        assert event.destination_channel is None

    def test_a_queued_payload_parses_as_this_event(self):
        task = _task(
            {
                "type": "customer_question",
                "text": "How do I add a retention policy?",
                "subject": "Retention",
                "platform": "Tiger Cloud",
                "source": "salesforce_case",
                "source_id": "500...",
            }
        )
        assert isinstance(task.event, CustomerQuestionEvent)
        assert task.event.platform == "Tiger Cloud"

    def test_a_slack_payload_still_parses_as_a_slack_event(self):
        task = _task(
            {
                "type": "app_mention",
                "text": "<@U_BOT> hi",
                "channel": "C_CHAN",
                "ts": "1700000000.000100",
                "event_ts": "1700000000.000100",
                "user": "U_USER",
            }
        )
        assert isinstance(task.event, SlackAppMentionEvent)


class TestDestinationChannelConvention:
    def test_slack_events_reply_where_they_arrived(self):
        event = SlackAppMentionEvent(
            ts="1", event_ts="1", text="hi", channel="C_CHAN", user="U"
        )
        assert event.destination_channel == "C_CHAN"

    def test_create_case_form_replies_in_its_channel(self):
        event = SalesforceCreateNewCaseEvent(
            subject="s", description="d", user="U", channel="C_FORM"
        )
        assert event.destination_channel == "C_FORM"

    def test_assignment_event_carries_the_channel_it_was_enqueued_with(self):
        event = SalesforceAssignmentChangedEvent(
            case={"Id": "500", "CaseNumber": "1"}, destination_channel="C_CASES"
        )
        assert event.destination_channel == "C_CASES"
        assert (
            SalesforceAssignmentChangedEvent(
                case={"Id": "500", "CaseNumber": "1"}
            ).destination_channel
            is None
        )
