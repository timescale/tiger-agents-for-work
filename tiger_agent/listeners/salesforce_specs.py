from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.listeners import salesforce as listener_module
from tiger_agent.listeners.salesforce import SalesforceListener
from tiger_agent.salesforce.types import CaseData, SalesforceUser

STREAM_CASE = CaseData(
    Id="500Nv00000ABCDE",
    Status="New",
    Owner=SalesforceUser(Id="005Nv00000USER1", Email="owner@tigerdata.com"),
)
FULL_CASE = CaseData(
    Id="500Nv00000ABCDE",
    CaseNumber="00012345",
    Subject="Cannot connect",
    Status="New",
    Origin="Email",
    Owner=SalesforceUser(Id="005Nv00000USER1", Email="owner@tigerdata.com"),
)


@pytest.fixture
def hctx():
    hctx = MagicMock()
    hctx.salesforce_client = MagicMock()
    hctx.pool = MagicMock()
    hctx.trigger = AsyncMock()
    return hctx


@pytest.fixture
def listener(hctx):
    return SalesforceListener(hctx=hctx)


@pytest.fixture(autouse=True)
def collaborators(monkeypatch):
    """Stub every module-level collaborator so the handlers run without
    Salesforce or the database; the decision under test is what happens after
    get_case."""
    stubs = {
        "get_case": MagicMock(return_value=FULL_CASE),
        "insert_event": AsyncMock(),
        "should_ignore_new_case": MagicMock(return_value=False),
        "is_case_assignment_new": AsyncMock(return_value=True),
        "is_case_status_change_new": AsyncMock(return_value=True),
        "get_salesforce_case_thread_thread_id": AsyncMock(
            return_value=["C_THREAD", "1700000000.000100"]
        ),
    }
    for name, stub in stubs.items():
        monkeypatch.setattr(listener_module, name, stub)
    monkeypatch.setattr(listener_module, "SALESFORCE_CASE_CHANNEL", "C_CASES")
    return stubs


def _inserted_event(collaborators) -> dict:
    collaborators["insert_event"].assert_awaited_once()
    return collaborators["insert_event"].await_args.kwargs["event"]


class TestHandleCaseCreated:
    async def test_enqueues_the_full_case(self, listener, hctx, collaborators):
        await listener.handle_case_created(STREAM_CASE)

        collaborators["get_case"].assert_called_once_with(
            hctx.salesforce_client, "500Nv00000ABCDE"
        )
        event = _inserted_event(collaborators)
        assert event["subtype"] == "case_created"
        assert event["case"]["CaseNumber"] == "00012345"
        hctx.trigger.put.assert_awaited_once_with(True)

    async def test_ignores_a_case_that_no_longer_exists(
        self, listener, hctx, collaborators
    ):
        collaborators["get_case"].return_value = None

        await listener.handle_case_created(STREAM_CASE)

        collaborators["insert_event"].assert_not_awaited()
        hctx.trigger.put.assert_not_awaited()


class TestHandleUpdatedCaseAssignee:
    async def test_enqueues_the_full_case(self, listener, hctx, collaborators):
        await listener.handle_updated_case_assignee(STREAM_CASE)

        event = _inserted_event(collaborators)
        assert event["subtype"] == "new_assignee"
        assert event["case"]["CaseNumber"] == "00012345"
        assert event["destination_channel"] == "C_CASES"
        hctx.trigger.put.assert_awaited_once_with(True)

    async def test_ignores_a_case_that_no_longer_exists(
        self, listener, hctx, collaborators
    ):
        collaborators["get_case"].return_value = None

        await listener.handle_updated_case_assignee(STREAM_CASE)

        collaborators["insert_event"].assert_not_awaited()
        hctx.trigger.put.assert_not_awaited()


class TestHandleCaseStatusChanged:
    async def test_enqueues_the_full_case_with_its_thread(
        self, listener, hctx, collaborators
    ):
        await listener.handle_case_status_changed(STREAM_CASE)

        event = _inserted_event(collaborators)
        assert event["subtype"] == "case_status_changed"
        assert event["case"]["CaseNumber"] == "00012345"
        assert event["slack_channel_id"] == "C_THREAD"
        assert event["slack_thread_ts"] == "1700000000.000100"
        hctx.trigger.put.assert_awaited_once_with(True)

    async def test_ignores_a_case_that_no_longer_exists(
        self, listener, hctx, collaborators
    ):
        collaborators["get_case"].return_value = None

        await listener.handle_case_status_changed(STREAM_CASE)

        collaborators["insert_event"].assert_not_awaited()
        hctx.trigger.put.assert_not_awaited()
