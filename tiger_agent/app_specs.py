from asyncio import Queue
from unittest.mock import MagicMock

import pytest

from tiger_agent.agent.constants import USER_DEFINED_EVENTS_ENABLED
from tiger_agent.app import _HANDLERS, TigerApp
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
    SlackAppMentionEvent,
    SlackMessageEvent,
    SlackSalesforceCaseThreadMessageEvent,
)
from tiger_agent.tasks.handlers import (
    AgentFeedbackRatingHandler,
    AgentFeedbackRequestReminderHandler,
    SalesforceAssignmentChangedHandler,
    SalesforceCaseCreatedHandler,
    SalesforceCaseStatusChangedHandler,
    SalesforceCreateCaseHandler,
    SalesforceFeedItemHandler,
    SlackSalesforceCaseThreadMessageHandler,
    SlackTaskHandler,
    TaskHandler,
    TaskProcessor,
    UserDefinedRuleMatchHandler,
)
from tiger_agent.types import HarnessContext

EXPECTED_EVENT_ROUTES: dict[type, type[TaskHandler]] = {
    SlackAppMentionEvent: SlackTaskHandler,
    SlackMessageEvent: SlackTaskHandler,
    SalesforceCaseCreatedEvent: SalesforceCaseCreatedHandler,
    SalesforceAssignmentChangedEvent: SalesforceAssignmentChangedHandler,
    SalesforceCreateNewCaseEvent: SalesforceCreateCaseHandler,
    SalesforceFeedItemEvent: SalesforceFeedItemHandler,
    SlackSalesforceCaseThreadMessageEvent: SlackSalesforceCaseThreadMessageHandler,
    SalesforceCaseStatusChangedEvent: SalesforceCaseStatusChangedHandler,
    AgentFeedbackRatingEvent: AgentFeedbackRatingHandler,
    AgentFeedbackRequestReminderEvent: AgentFeedbackRequestReminderHandler,
    **(
        {UserDefinedRuleMatch: UserDefinedRuleMatchHandler}
        if USER_DEFINED_EVENTS_ENABLED
        else {}
    ),
}


class TestHandlersRegistry:
    def test_every_handler_declares_event_types(self):
        for handler_cls in _HANDLERS:
            assert hasattr(handler_cls, "EVENT_TYPES"), (
                f"{handler_cls.__name__} missing EVENT_TYPES"
            )
            assert isinstance(handler_cls.EVENT_TYPES, list)
            assert len(handler_cls.EVENT_TYPES) > 0

    def test_no_event_type_registered_twice(self):
        seen: dict[type, type[TaskHandler]] = {}
        for handler_cls in _HANDLERS:
            for event_type in handler_cls.EVENT_TYPES:
                assert event_type not in seen, (
                    f"{event_type.__name__} registered by both "
                    f"{seen[event_type].__name__} and {handler_cls.__name__}"
                )
                seen[event_type] = handler_cls

    def test_handlers_cover_all_expected_event_types(self):
        registered = {
            event_type: handler_cls
            for handler_cls in _HANDLERS
            for event_type in handler_cls.EVENT_TYPES
        }
        assert registered == EXPECTED_EVENT_ROUTES


@pytest.fixture
def mock_hctx(make_bot_info):
    return HarnessContext(
        app=MagicMock(),
        pool=MagicMock(),
        trigger=Queue(),
        bot_info=make_bot_info(),
    )


@pytest.fixture
def mock_agent():
    return MagicMock()


class TestTigerAppRegistration:
    def test_constructs_without_error_when_hctx_and_agent_provided(
        self, mock_hctx, mock_agent
    ):
        app = TigerApp(hctx=mock_hctx, agent=mock_agent)

        assert app._hctx is mock_hctx

    def test_registers_every_event_type_from_expected_routes(
        self, mock_hctx, mock_agent, monkeypatch
    ):
        # Capture the processor that TigerApp constructs so we can inspect its
        # populated handler map without reaching through _listener_harness.
        captured: dict = {}
        real_register = TaskProcessor.register

        def spy_register(self, event_types, handler):
            captured.setdefault("processor", self)
            return real_register(self, event_types, handler)

        monkeypatch.setattr(TaskProcessor, "register", spy_register)

        TigerApp(hctx=mock_hctx, agent=mock_agent)

        processor = captured["processor"]
        registered_types = set(processor._handlers.keys())
        assert registered_types == set(EXPECTED_EVENT_ROUTES.keys())

        for event_type, expected_handler_cls in EXPECTED_EVENT_ROUTES.items():
            handler = processor._handlers[event_type]
            assert isinstance(handler, expected_handler_cls), (
                f"{event_type.__name__} routed to "
                f"{type(handler).__name__}, expected {expected_handler_cls.__name__}"
            )
